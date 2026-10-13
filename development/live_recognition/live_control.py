"""Scoped acc1 validation using existing controller, identity checks and Stop latch."""
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import farm

OUT = Path(__file__).resolve().parent

def log(action, **values):
    record = {'time': time.time(), 'action': action, **values}
    with (OUT / 'actions.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record) + '\n')
    print(json.dumps(record), flush=True)

def snapshot(tag):
    device = farm.Device()
    device.verify()
    if device.foreground() != farm.PACKAGE:
        raise RuntimeError('Game is not foreground; no input permitted')
    frame = device.capture()
    state = farm.read_json(farm.RUNTIME / 'supervisor.json')
    stage = state.get('stage') or farm.selected_stage()
    screen, _, _ = farm.Detector(stage).classify(frame)
    path = OUT / (tag + '.png')
    if path.exists():
        raise RuntimeError('Do not overwrite validation evidence')
    farm.cv2.imwrite(str(path), frame)
    log('verified_acc1_capture', screen=screen, stage=stage, screenshot=str(path), inputs_sent=False)
    return stage, screen

def reload():
    if farm.STOP.exists():
        raise RuntimeError('Stop is already requested; validation will not restart farming')
    stage, screen = snapshot('before_reload_' + str(int(time.time())))
    if screen not in {'READY_TO_RETRY', 'STAMINA_PROMPT', 'IN_BATTLE', 'RESULTS_NEXT', 'REWARD_REVEAL'}:
        raise RuntimeError('Unexpected screen; no development game input or restart issued')
    token = 'Recognition validation controlled stop: ' + uuid.uuid4().hex
    with farm.lock('control'):
        if farm.STOP.exists():
            raise RuntimeError('A Stop request arrived; validation cancelled')
        farm.STOP.write_text(token, encoding='utf-8')
    log('controlled_stop_requested', stage=stage, screen=screen)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if farm.STOP.read_text('utf-8') != token:
            raise RuntimeError('User Stop superseded validation; will not restart')
        if not farm.live_controller_pids() and not farm.lock_busy('worker') and not farm.lock_busy('supervisor'):
            break
        time.sleep(.25)
    else:
        raise RuntimeError('Controller did not exit; Stop remains requested')
    if farm.STOP.read_text('utf-8') != token:
        raise RuntimeError('User Stop superseded validation; will not restart')
    if farm.selected_stage() != stage:
        raise RuntimeError('User stage selection changed; development restart cancelled')
    # Preserve a small, bounded set of new run frames for actual live review.
    os.environ['NBR_LOOT_VALIDATION_DIR'] = str(OUT / 'live_runs')
    os.environ['NBR_LOOT_VALIDATION_MAX_RUNS'] = '4'
    log('reload_start_requested', stage=stage)
    farm.start(stage, expected_stop=token)
    state = farm.read_json(farm.RUNTIME / 'supervisor.json')
    log('reload_started', stage=stage, supervisor_pid=state.get('pid'), worker_pid=state.get('worker_pid'))

def stop_for_validation():
    farm.Device().verify()
    token = 'Recognition validation controlled stop: ' + uuid.uuid4().hex
    with farm.lock('control'):
        if farm.STOP.exists():
            raise RuntimeError('Stop already requested; user controls take precedence')
        farm.STOP.write_text(token, encoding='utf-8')
    (OUT/'owned_stop.json').write_text(json.dumps({'token':token,'time':time.time()}),encoding='utf-8')
    log('controlled_stop_requested', reason='Wait for multiplayer result, then return to solo farming; no game input')
    deadline = time.monotonic()+40
    while time.monotonic()<deadline:
        if not farm.STOP.exists() or farm.STOP.read_text('utf-8') != token:
            raise RuntimeError('User control superseded validation; no automatic restart')
        if not farm.live_controller_pids() and not farm.lock_busy('worker') and not farm.lock_busy('supervisor'):
            log('controlled_stop_completed', game_left_running=True)
            return
        time.sleep(.25)
    raise RuntimeError('Controller exit timed out; Stop remains requested')

def start_solo():
    owned = json.loads((OUT/'owned_stop.json').read_text('utf-8'))
    token = owned['token']
    stage = farm.selected_stage()
    if stage != 'practice-part-1':
        raise RuntimeError('This reviewed start applies only to the prepared Practice Training solo stage')
    device = farm.Device()
    detector = farm.Detector(stage)
    with farm.lock('control'):
        if not farm.STOP.exists() or farm.STOP.read_text('utf-8') != token:
            raise RuntimeError('User Stop superseded validation; no start permitted')
        if farm.live_controller_pids() or farm.lock_busy('worker') or farm.lock_busy('supervisor'):
            raise RuntimeError('Controller active; manual input refused')
        device.verify()
        for _ in range(2):
            if device.foreground() != farm.PACKAGE:
                raise RuntimeError('Game foreground changed')
            frame = device.capture()
            name, spec, _ = detector.classify(frame)
            if name != 'READY_TO_START' or not spec or spec['action']['id'] != 'start_mission':
                raise RuntimeError('Prepared solo Start Mission screen no longer verified')
            time.sleep(.25)
        log('solo_start_mission_input', stage=stage, controller_action=spec['action']['id'], point=spec['action']['point'])
        farm.STOP.unlink()
        try:
            device.perform(spec['action'])
        except Exception:
            if not farm.STOP.exists():
                farm.STOP.write_text(token, encoding='utf-8')
            raise
        if farm.STOP.exists():
            raise RuntimeError('Stop request arrived; farming will not be started')
        farm.STOP.write_text(token, encoding='utf-8')
    os.environ['NBR_LOOT_VALIDATION_DIR'] = str(OUT/'live_runs')
    os.environ['NBR_LOOT_VALIDATION_MAX_RUNS'] = '4'
    farm.start(stage, expected_stop=token)
    state = farm.read_json(farm.RUNTIME/'supervisor.json')
    log('solo_farming_started', stage=stage, supervisor_pid=state.get('pid'), worker_pid=state.get('worker_pid'))

if __name__ == '__main__':
    try:
        if sys.argv[1] == 'reload':
            reload()
        elif sys.argv[1] == 'stop':
            stop_for_validation()
        elif sys.argv[1] == 'start-solo':
            start_solo()
        elif sys.argv[1] == 'capture':
            if farm.STOP.exists():
                owned = OUT/'owned_stop.json'
                if not owned.exists() or farm.STOP.read_text('utf-8') != json.loads(owned.read_text('utf-8'))['token']:
                    raise RuntimeError('User Stop requested; validation cancelled')
            snapshot('capture_' + str(int(time.time())))
        else:
            raise ValueError('Use reload or capture')
    except Exception as exc:
        log('validation_action_failed', error=str(exc))
        raise SystemExit(1)
