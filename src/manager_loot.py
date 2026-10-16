"""Read-only library artwork lookup and compact acquired-loot display."""
from __future__ import annotations

from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)


def _local_image(root, value):
    """Library paths may only reference images inside that library directory."""
    if not isinstance(value, str) or not value:
        return None
    try:
        path = (root / value).resolve()
        if (not path.is_relative_to(root) or not path.is_file()
                or path.suffix.lower() not in {'.png', '.webp', '.jpg', '.jpeg', '.gif', '.bmp'}):
            return None
        return str(path)
    except (OSError, ValueError):
        return None


def _canonical_id(item_id, aliases):
    seen = set()
    while item_id in aliases:
        if item_id in seen:
            return None
        seen.add(item_id)
        item_id = aliases[item_id]
    return item_id


def _eligible(record, asset):
    # Character ability icons and cut-ins belong to the same record, but are
    # never a portrait of a dropped unit. Items use their compact card or icon.
    if record['record_type'] == 'character':
        from item_recognition.reward_identity import _character_asset
        return _character_asset(record, dict(asset))
    return record['record_type'] == 'item' and asset['role'] in {'card', 'icon', 'image'}


def enrich_loot_items(root, items):
    """Copy dashboard items and resolve exact library IDs in a worker thread.

    No library is created or modified. Names, neighboring game IDs, and artwork
    similarity never establish identity. Explicit library aliases are honored.
    The returned paths are local library files; Qt decoding remains in LootGrid.
    """
    result = [dict(item) for item in items]
    for item in result:
        item['icon_path'] = None
        item['icon_asset_id'] = None
        item['library_record_id'] = None
        item['icon_candidates'] = []
    library_root = (Path(root) / 'runtime' / 'library').resolve()
    database = library_root / 'data' / 'library.sqlite3'
    if not result or not database.is_file():
        return result
    try:
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.1)) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            aliases = dict(db.execute('SELECT alias_id,canonical_id FROM identity_aliases')) if 'identity_aliases' in tables else {}
            cache = {}
            for item in result:
                rid = item.get('item_id')
                if not isinstance(rid, str):
                    continue
                canonical = _canonical_id(rid, aliases)
                if canonical is None:
                    continue
                if canonical not in cache:
                    row = db.execute('SELECT id,record_type,metadata FROM records WHERE id=?', (canonical,)).fetchone()
                    record = dict(row) if row else None
                    candidates = []
                    if record:
                        record['metadata'] = json.loads(record['metadata'])
                        members = [canonical, *(alias for alias in aliases if _canonical_id(alias, aliases) == canonical)]
                        slots = ','.join('?' for _ in members)
                        preferences = dict(db.execute('SELECT asset_id,fidelity_score FROM asset_preferences WHERE record_id=?', (canonical,))) if 'asset_preferences' in tables else {}
                        assets = db.execute(
                            'SELECT a.*,ra.role FROM assets a JOIN record_assets ra ON ra.asset_id=a.id '
                            f'WHERE ra.record_id IN ({slots}) AND a.status=?', [*members, 'ok']).fetchall()
                        role_order = {'card': 0, 'portrait': 1, 'icon': 2, 'image': 3}
                        assets = sorted(assets, key=lambda a: (
                            -float(preferences.get(a['id']) or 0), role_order.get(a['role'], 9), a['id']))
                        seen = set()
                        for asset in assets:
                            if not _eligible(record, asset):
                                continue
                            for field in ('local_image_path', 'thumbnail_path'):
                                path = _local_image(library_root, asset[field])
                                if path and path not in seen:
                                    seen.add(path)
                                    candidates.append({'path': path, 'asset_id': asset['id']})
                    cache[canonical] = (canonical if record else None, candidates)
                library_id, candidates = cache[canonical]
                item['library_record_id'] = library_id
                item['icon_candidates'] = [dict(candidate) for candidate in candidates]
                if candidates:
                    item['icon_path'] = candidates[0]['path']
                    item['icon_asset_id'] = candidates[0]['asset_id']
    except (sqlite3.Error, OSError, ValueError, TypeError):
        # Missing, busy or older libraries must not hide the recorded rewards.
        pass
    return result


def acquired_count(item):
    """Each recognized acquired icon is one drop, independent of text quantities."""
    count = item.get('drop_count', item.get('observed_icons'))
    if type(count) is not int or count < 0:
        return '?', 'Drop count unavailable.'
    label = 'drop' if count == 1 else 'drops'
    return str(count), f'{count} {label}. Each acquired icon counts as one drop.'


def sort_loot_items(items, order):
    """Sort display copies; uncertain identities stay after identified drops."""
    if order not in {'type', 'rarity', 'recent'}:
        raise ValueError('Unknown drop sort order')

    def number(value):
        try:
            value = float(value) if not isinstance(value, bool) else 0
            return value if math.isfinite(value) else 0
        except (ValueError, TypeError):
            return 0

    def key(item):
        element = str(item.get('element') or '').casefold()
        kind = (str(item.get('record_type') or 'item'), not bool(element), element)
        rarity = -number(item.get('rarity'))
        name = str(item.get('item_name') or item.get('item_id') or '').casefold()
        if order == 'recent':
            primary = (-number(item.get('last_drop_sequence')), -number(item.get('last_drop_at')))
        elif order == 'rarity':
            primary = (rarity, *kind)
        else:
            primary = (*kind, rarity)
        return (not bool(item.get('item_id')), *primary, name, str(item.get('item_id') or ''))

    return sorted(items, key=key)


def _two_line_name(name, metrics, width):
    if metrics.horizontalAdvance(name) <= width:
        return name
    words = name.split()
    first = []
    while words and metrics.horizontalAdvance(' '.join([*first, words[0]])) <= width:
        first.append(words.pop(0))
    if not first:
        return metrics.elidedText(name, Qt.TextElideMode.ElideRight, width)
    return ' '.join(first) + '\n' + metrics.elidedText(' '.join(words), Qt.TextElideMode.ElideRight, width)


class LootTile(QFrame):
    """A library icon with an acquired-count badge and a readable item name."""
    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = dict(item)
        self.setObjectName('lootTile')
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedWidth(104)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        name = str(item.get('item_name') or item.get('item_id') or 'Unknown item')
        count, explanation = acquired_count(item)
        tooltip = f'{name}\n{explanation}'
        if item.get('item_id'):
            tooltip += f"\n{item['item_id']}"
        self.setToolTip(tooltip)
        self.setAccessibleName(f'{name}: {explanation}')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(4)
        art = QWidget(self)
        art.setFixedSize(92, 72)
        self.icon_label = QLabel(art)
        self.icon_label.setObjectName('lootIcon')
        self.icon_label.setGeometry(10, 0, 72, 72)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_asset_id = None
        self.icon_path = None
        candidates = item.get('icon_candidates') or [{'path': item.get('icon_path'), 'asset_id': item.get('icon_asset_id')}]
        for candidate in candidates:
            path = candidate.get('path')
            if not path:
                continue
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self.icon_label.setPixmap(pixmap.scaled(
                    72, 72, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                self.icon_asset_id, self.icon_path = candidate.get('asset_id'), path
                break
        if self.icon_path is None:
            self.icon_label.setText('?')
            self.icon_label.setStyleSheet('font-size: 28px; font-weight: bold;')
            self.icon_label.setToolTip('Library artwork unavailable')
        self.count_label = QLabel(count, art)
        self.count_label.setObjectName('lootCount')
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_label.setStyleSheet(
            'QLabel { background: #172337; color: #ffffff; border: 1px solid #b7c7df; '
            'border-radius: 7px; padding: 1px 5px; font-weight: bold; font-size: 12px; }')
        self.count_label.adjustSize()
        self.count_label.move(92 - self.count_label.width(), 72 - self.count_label.height())
        self.count_label.setToolTip(explanation)
        self.count_label.raise_()
        layout.addWidget(art)
        self.name_label = QLabel(self)
        self.name_label.setTextFormat(Qt.TextFormat.PlainText)
        self.name_label.setObjectName('lootItemName')
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.name_label.setWordWrap(False)
        self.name_label.setToolTip(tooltip)
        layout.addWidget(self.name_label)
        self._update_name()

    def _update_name(self):
        metrics = self.name_label.fontMetrics()
        name = str(self.item.get('item_name') or self.item.get('item_id') or 'Unknown item')
        self.name_label.setText(_two_line_name(name, metrics, 92))
        self.name_label.setFixedHeight(metrics.lineSpacing() * 2)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in {QEvent.Type.FontChange, QEvent.Type.StyleChange} and hasattr(self, 'name_label'):
            self._update_name()


class LootGrid(QScrollArea):
    """Always-visible, responsive loot tiles. set_items() performs no DB work."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('lootGrid')
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumHeight(132)
        self.setMaximumHeight(272)
        self._content = QWidget(self)
        self._content.setObjectName('lootContents')
        self._layout = QGridLayout(self._content)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.setWidget(self._content)
        self.tiles = []
        self.items = []
        self._columns = 0
        self.empty_label = QLabel('No loot yet', self._content)
        self.empty_label.setObjectName('muted')
        self._layout.addWidget(self.empty_label, 0, 0)

    def set_items(self, items):
        items = [dict(item) for item in items]
        if items == self.items:
            return
        self.items = items
        for tile in self.tiles:
            self._layout.removeWidget(tile)
            tile.hide()
            tile.deleteLater()
        self.tiles = [LootTile(item, self._content) for item in items]
        self.empty_label.setVisible(not self.tiles)
        self._columns = 0
        self._arrange()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange()

    def _arrange(self):
        columns = max(1, (self.viewport().width() + 8) // 112)
        if columns == self._columns:
            return
        self._columns = columns
        for index, tile in enumerate(self.tiles):
            self._layout.addWidget(tile, index // columns, index % columns)
            tile.show()
