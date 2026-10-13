"""Local, model-free alert. Dismissing it does not restart farming."""
import ctypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ctypes.windll.user32.MessageBoxW(
    None,
    'acc1 farming stopped because a loss or revival prompt was detected.\n\n'
    'Please fix the game manually. No revival, currency, or exit control was selected.\n'
    f'When ready, run {ROOT / "scripts" / "nbr.ps1"} start.\n\n'
    f'Evidence and status: {ROOT / "runtime" / "loss.json"}',
    'Naruto Blazing Revival — farming stopped',
    0x00000030 | 0x00010000,
)
