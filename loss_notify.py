"""Local, model-free alert. Dismissing it does not restart farming."""
import ctypes
ctypes.windll.user32.MessageBoxW(
    None,
    'acc1 farming stopped because a loss or revival prompt was detected.\n\n'
    'Please fix the game manually. No revival, currency, or exit control was selected.\n'
    'When ready, run D:\\NBR\\nbr.ps1 start.\n\n'
    'Evidence and status: D:\\NBR\\runtime\\loss.json',
    'Naruto Blazing Revival — farming stopped',
    0x00000030 | 0x00010000,
)
