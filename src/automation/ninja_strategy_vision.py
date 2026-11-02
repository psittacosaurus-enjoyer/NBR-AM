"""Conservative visual observations used only by the Stage 15 strategy."""
from __future__ import annotations

from automation import controller as farm
from automation.ninja_boss_swap import BossSwap


class Stage15Vision:
    """Reuse the game's existing counter, setting lamps and portrait geometry.

    A chakra value measures the visible blue ring, not a character's jutsu cost.
    The caller must observe a substantial drop for the same character and role;
    an initially empty ring does not prove that a jutsu was used.
    """

    def __init__(self, engine):
        self.engine = engine
        self.boss = BossSwap(engine)
        self._images = {}

    def _image(self, relative):
        path = farm.ROOT / relative
        signature = farm.Detector.file_signature([path])
        cached = self._images.get(relative)
        if cached is None or cached[0] != signature:
            image = farm.cv2.imread(str(path), farm.cv2.IMREAD_COLOR)
            if image is None:
                raise RuntimeError("Missing Stage 15 visual reference: " + relative)
            if signature != farm.Detector.file_signature([path]):
                raise RuntimeError("Stage 15 visual reference changed while loading")
            self._images[relative] = (signature, image)
        return self._images[relative][1]

    @staticmethod
    def _valid(frame):
        return frame is not None and frame.shape == (1600, 900, 3)

    @staticmethod
    def _ink(image):
        hsv = farm.cv2.cvtColor(image, farm.cv2.COLOR_BGR2HSV)
        return farm.cv2.inRange(hsv, farm.np.array([0, 0, 130]),
                               farm.np.array([179, 120, 255])) > 0

    @staticmethod
    def _overlap(left, right):
        union = farm.np.count_nonzero(left | right)
        return float(farm.np.count_nonzero(left & right) / union) if union else 0.

    def read_map(self, frame):
        if not self._valid(frame):
            return None
        known = self.engine.read_ready_map(frame)
        if known is not None:
            return known
        # These are actual glyphs from the existing map references, not OCR or
        # generated full-counter references. The unchanged /20 suffix anchors
        # the reading and the two digit cells have fixed observed positions.
        references = {number: self._ink(self._image(
            "assets/patterns/ninja_road/maps/" + str(number) + ".png"))
            for number in range(2, 12)}
        ink = self._ink(frame[8:46, 245:348])
        if max(self._overlap(ink[7:34, 50:98], ref[7:34, 50:98])
               for ref in references.values()) < .82:
            return None
        glyphs = {str(number): reference[7:34, 33:51]
                  for number, reference in references.items() if number < 10}
        glyphs["0"] = references[10][7:34, 33:51]
        glyphs["1"] = references[11][7:34, 33:51]

        def digit(patch):
            ranked = sorted(((self._overlap(patch, reference), number)
                             for number, reference in glyphs.items()), reverse=True)
            return ranked[0][1] if ranked[0][0] >= .78 and ranked[0][0] - ranked[1][0] >= .10 else None

        ones = digit(ink[7:34, 33:51])
        if ones is None:
            return None
        tens_patch = ink[7:34, 15:33]
        if farm.np.count_nonzero(tens_patch) < 15:
            value = int(ones)
        else:
            tens = digit(tens_patch)
            if tens != "1":
                return None
            value = 10 + int(ones)
        return value if 1 <= value <= 16 else None

    def setting(self, frame, name):
        if name not in {"ninjutsu", "secret"}:
            raise ValueError("Unknown Ninja Road setting")
        if not self._valid(frame):
            return None
        x = 75 if name == "ninjutsu" else 369
        region = (100, 1390, 270, 1435) if name == "ninjutsu" else (398, 1380, 568, 1438)
        x1, y1, x2, y2 = region
        label = farm.cv2.cvtColor(frame[y1:y2, x1:x2], farm.cv2.COLOR_BGR2GRAY)
        reference = farm.cv2.cvtColor(self._image(
            "assets/patterns/ninja_road/stage15/" + name + "_label.png"), farm.cv2.COLOR_BGR2GRAY)
        correlation = float(farm.cv2.matchTemplate(label, reference, farm.cv2.TM_CCOEFF_NORMED)[0, 0])
        difference = float(farm.np.abs(label.astype(farm.np.int16) - reference).mean())
        # Enabled labels acquire a yellow glow; label identity is only a panel
        # guard. The separate lamp below determines the actual setting value.
        if not farm.np.isfinite(correlation) or correlation < .92 or difference > 45:
            return None  # A manual portrait or modal is not a setting panel.
        hsv = farm.cv2.cvtColor(frame[1404:1426, x-11:x+11], farm.cv2.COLOR_BGR2HSV)
        gold = farm.cv2.countNonZero(farm.cv2.inRange(hsv, farm.np.array([12, 100, 160]),
                                                   farm.np.array([40, 255, 255])))
        white = farm.cv2.countNonZero(farm.cv2.inRange(hsv, farm.np.array([0, 0, 170]),
                                                    farm.np.array([179, 65, 255])))
        if gold >= 100:
            return True
        if gold <= 10 and white >= 80:
            return False
        return None

    @staticmethod
    def team_panel(frame):
        # Compare only face interiors and element badges. Chakra depletion,
        # health changes, and turn-scroll animation cannot prove a team change.
        patches = []
        for column in range(3):
            offset = 295 * column
            for x1, y1, x2, y2 in ((145, 1395, 205, 1447), (42, 1460, 100, 1517),
                                   (78, 1330, 94, 1346), (23, 1410, 39, 1426)):
                gray = farm.cv2.cvtColor(frame[y1:y2, x1+offset:x2+offset], farm.cv2.COLOR_BGR2GRAY)
                patches.append(farm.cv2.resize(gray, (44, 44), interpolation=farm.cv2.INTER_AREA))
        return farm.np.hstack(patches)

    def chakra(self, frame, column):
        if type(column) is not int or column not in (0, 1, 2):
            raise ValueError("Invalid Ninja Road character column")
        if not self._valid(frame) or self.boss.auto(frame) is not False:
            return None  # AUTO PLAY covers most of the chakra ring.
        # Sample angular coverage rather than pixel area: the turn-selected
        # portrait expands. Reserve portraits obscure the lower-left sector.
        angles = farm.np.deg2rad(farm.np.arange(-170, 131, 5))
        radii = farm.np.arange(67, 106)
        xx = farm.np.rint(174 + 295 * column + farm.np.cos(angles)[:, None] * radii).astype(int)
        yy = farm.np.rint(1420 + farm.np.sin(angles)[:, None] * radii).astype(int)
        hsv = farm.cv2.cvtColor(frame[yy.min():yy.max()+1, xx.min():xx.max()+1], farm.cv2.COLOR_BGR2HSV)
        samples = hsv[yy-yy.min(), xx-xx.min()]
        blue = ((samples[:, :, 0] >= 85) & (samples[:, :, 0] <= 120)
                & (samples[:, :, 1] >= 100) & (samples[:, :, 2] >= 70))
        # Require recognizable gold scroll structure. Dark frames, hidden HUDs,
        # and character animations are unknown, never an empty chakra gauge.
        rim = ((samples[:, :, 0] >= 15) & (samples[:, :, 0] <= 40)
               & (samples[:, :, 1] >= 70) & (samples[:, :, 2] >= 80))
        if farm.np.count_nonzero(rim) < 120:
            return None
        return float(farm.np.mean(farm.np.count_nonzero(blue, axis=1) >= 8))
