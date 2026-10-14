"""Shared farming screen guards built from the existing verified layouts.

Mission titles identify a mission optionally; they never authorize an input.
Menu navigation is deliberately excluded. No new combat behavior lives here.
"""
import copy
import json


TITLE_INDEX = {'READY_TO_START': 1, 'READY_TO_RETRY': 1, 'RESULTS_NEXT': 1,
               'REWARD_REVEAL': 0, 'RANK_UP_STAMINA_ACK': 2, 'STAMINA_PROMPT': 3}
SHARED = {*TITLE_INDEX, 'IN_BATTLE', 'MISSION_COMPLETE', 'COMMUNICATION_ERROR'}
SHARED_CATALOGS = (('catalog.json', 'scrolls-a'), ('rich_b.json', 'rich-b'),
                   ('night_of_tragedy_a.json', 'night-of-tragedy-a'))


def catalog_paths(root):
    return [root / 'assets/patterns' / name
            for name in [*(name for name, _ in SHARED_CATALOGS), 'universal_results.json']]


def shared_states(root):
    states, titles, seen = [], [], set()
    for catalog, mission in SHARED_CATALOGS:
        source = json.loads((root / 'assets/patterns' / catalog).read_text(encoding='utf-8'))
        for original in source['states']:
            if original['name'] not in SHARED:
                continue
            state = copy.deepcopy(original)
            index = TITLE_INDEX.get(state['name'])
            if index is not None:
                # Only catalogs with a verified title guard can supply a variant.
                if len(state['anchors']) <= index:
                    continue
                title = state['anchors'].pop(index)
                titles.append((state['name'], mission, title))
                if mission == 'scrolls-a':
                    practice = copy.deepcopy(title)
                    start = state['name'] == 'READY_TO_START'
                    practice['region'] = [190,395,735,461] if start else [190,182,735,247]
                    variant = 'start_title' if start else 'results_title'
                    if state['name'] in {'STAMINA_PROMPT', 'RANK_UP_STAMINA_ACK'}:
                        variant = 'dialog_title'
                    practice['template'] = f'assets/patterns/practice_part_1/{variant}.png'
                    titles.append((state['name'], 'practice-part-1', practice))
                action = state.get('action', {})
                if action.get('point_anchor') is not None:
                    if action['point_anchor'] == index:
                        raise RuntimeError('Mission title cannot be an input target')
                    if action['point_anchor'] > index:
                        action['point_anchor'] -= 1
            if state['name'] == 'COMMUNICATION_ERROR':
                state['action']['expected'] = ['READY_TO_START', 'READY_TO_RETRY', 'RESULTS_NEXT',
                                               'REWARD_REVEAL', 'IN_BATTLE', 'MISSION_COMPLETE']
            if state['name'] == 'RESULTS_NEXT':
                state['action']['expected'].append('TRIAL_COINS_NEXT')
            # Identical guards from different missions are a single shared state.
            key = json.dumps({k: state.get(k) for k in ('name','anchors','priority','action')}, sort_keys=True)
            if key not in seen:
                seen.add(key)
                state['notes'] = 'Shared verified layout; mission-title recognition is optional.'
                states.append(state)
    # Results variants are shared by layout, never selected by mission identity.
    extra = json.loads((root / 'assets/patterns/universal_results.json').read_text(encoding='utf-8'))
    states.extend(extra['states'])
    return states, titles
