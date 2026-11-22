using System.Runtime.InteropServices;

namespace WinCap;

/// <summary>
/// Test-only helper: a small opaque window that paints a known 2x2 colour pattern and can change
/// it on demand. Used to prove that WGC returns live window content while the window is covered.
/// </summary>
public sealed class TestTargetWindow : IDisposable
{
    private const int WM_PAINT = 0x000F;
    private const int WM_ERASEBKGND = 0x0014;

    private static NativeMethods.WndProcDelegate s_wndProc = WndProc;
    private static readonly object s_lock = new();
    private static string s_className;
    private static int s_phase;
    private static IntPtr s_liveHwnd;

    private IntPtr _hwnd;
    private int _w, _h;

    public IntPtr Handle => _hwnd;
    public int Width => _w;
    public int Height => _h;

    public static TestTargetWindow Create(int x, int y, int width, int height, uint style = 0x90000000, uint exStyle = 0x00000000)
    {
        lock (s_lock)
        {
            if (s_className == null)
            {
                s_className = "WinCapTarget_" + Guid.NewGuid().ToString("N")[..8];
                var wc = new NativeMethods.WNDCLASSEX
                {
                    cbSize = (uint)Marshal.SizeOf<NativeMethods.WNDCLASSEX>(),
                    lpfnWndProc = Marshal.GetFunctionPointerForDelegate(s_wndProc),
                    hInstance = NativeMethods.GetModuleHandleW(null),
                    hbrBackground = NativeMethods.CreateSolidBrush(0x00202020),
                    lpszClassName = s_className,
                };
                if (NativeMethods.RegisterClassExW(ref wc) == 0)
                    throw new InvalidOperationException("RegisterClassEx failed: " + Marshal.GetLastWin32Error());
            }
        }

        var t = new TestTargetWindow { _w = width, _h = height };
        t._hwnd = NativeMethods.CreateWindowExW(exStyle, s_className, "WinCapTestTarget",
            style,
            x, y, width, height, IntPtr.Zero, IntPtr.Zero, NativeMethods.GetModuleHandleW(null), IntPtr.Zero);
        if (t._hwnd == IntPtr.Zero)
            throw new InvalidOperationException("CreateWindowEx failed: " + Marshal.GetLastWin32Error());

        s_liveHwnd = t._hwnd;
        NativeMethods.ShowWindow(t._hwnd, NativeMethods.SW_SHOWNOACTIVATE);
        NativeMethods.SetWindowPos(t._hwnd, NativeMethods.HWND_TOP, x, y, width, height,
            NativeMethods.SWP_NOACTIVATE | NativeMethods.SWP_SHOWWINDOW);
        t.SetPhase(1);
        return t;
    }

    /// <summary>Changes the painted pattern and forces an immediate repaint.</summary>
    public void SetPhase(int phase)
    {
        s_phase = phase;
        NativeMethods.InvalidateRect(_hwnd, IntPtr.Zero, false);
        NativeMethods.UpdateWindow(_hwnd);
    }

    public void Hide() => NativeMethods.ShowWindow(_hwnd, NativeMethods.SW_HIDE);

    private static IntPtr WndProc(IntPtr hwnd, uint msg, IntPtr wParam, IntPtr lParam)
    {
        if (msg == WM_ERASEBKGND) return (IntPtr)1;
        if (msg == WM_PAINT)
        {
            IntPtr hdc = NativeMethods.BeginPaint(hwnd, out var ps);
            try
            {
                NativeMethods.GetClientRect(hwnd, out var rc);
                int halfW = rc.Width / 2, halfH = rc.Height / 2;
                uint phaseColor = (s_phase % 4) switch
                {
                    0 => 0x0000FF, // red   (BGR)
                    1 => 0x00FF00, // green
                    2 => 0xFF0000, // blue
                    _ => 0x00FFFF, // yellow
                };
                Fill(hdc, 0, 0, halfW, halfH, 0x000080);
                Fill(hdc, halfW, 0, halfW, halfH, 0x008000);
                Fill(hdc, 0, halfH, halfW, halfH, 0x800000);
                Fill(hdc, halfW, halfH, halfW, halfH, phaseColor);
                Fill(hdc, rc.Width / 2 - 40, rc.Height / 2 - 40, 80, 80, 0xFFFFFF);
            }
            finally { NativeMethods.EndPaint(hwnd, ref ps); }
            return IntPtr.Zero;
        }
        return NativeMethods.DefWindowProcW(hwnd, msg, wParam, lParam);
    }

    private static void Fill(IntPtr hdc, int x, int y, int w, int h, uint bgr)
    {
        IntPtr brush = NativeMethods.CreateSolidBrush(bgr);
        var r = new NativeMethods.RECT { Left = x, Top = y, Right = x + w, Bottom = y + h };
        NativeMethods.FillRect(hdc, ref r, brush);
        NativeMethods.DeleteObject(brush);
    }

    public void Dispose()
    {
        if (_hwnd != IntPtr.Zero)
        {
            if (s_liveHwnd == _hwnd) s_liveHwnd = IntPtr.Zero;
            NativeMethods.DestroyWindow(_hwnd);
            _hwnd = IntPtr.Zero;
        }
    }
}
