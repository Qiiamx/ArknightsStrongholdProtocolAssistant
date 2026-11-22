using System.Runtime.InteropServices;

namespace WinCap;

/// <summary>Small window-state helpers. None of these are needed for capture itself.</summary>
public static class WindowTools
{
    /// <summary>
    /// Best-effort SetForegroundWindow. Windows may refuse if the calling process does not own
    /// the current foreground window; the return value says whether it succeeded.
    /// </summary>
    public static bool TrySetForeground(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero) return false;
        try { return NativeMethods.SetForegroundWindow(hwnd); }
        catch { return false; }
    }

    /// <summary>True when the window is the active (foreground) window.</summary>
    public static bool IsForeground(IntPtr hwnd) => NativeMethods.GetForegroundWindow() == hwnd;

    /// <summary>Which top-level window currently owns the pixels at a screen point.</summary>
    public static (long hwnd, int pid, string title) TopWindowAt(int x, int y) => WindowFinder.TopWindowAt(x, y);
}
