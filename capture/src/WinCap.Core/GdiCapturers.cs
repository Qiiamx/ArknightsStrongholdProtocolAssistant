using System.Runtime.InteropServices;

namespace WinCap;

/// <summary>Result of a capture attempt: image plus a human-readable failure reason when it failed.</summary>
public sealed class CaptureResult
{
    public bool Ok { get; init; }
    public string Method { get; init; } = "";
    public string Error { get; init; }
    public FrameImage Image { get; init; }
    public string Notes { get; init; } = "";
    public double CaptureMs { get; init; }
    public double EncodeMs { get; init; }
    public string Path { get; init; }

    public static CaptureResult Fail(string method, string error) => new() { Ok = false, Method = method, Error = error };
}

/// <summary>
/// Alternatives to WGC. All of these are window/screen reads through GDI, which on a
/// DWM-composited desktop means "whatever is currently visible on screen" - i.e. they are
/// expected to be wrong or blank for an occluded hardware-accelerated window. They are
/// implemented so the claim can be measured rather than assumed.
/// </summary>
internal static class GdiCapture
{
    internal static FrameImage ReadBitmap(IntPtr hdc, IntPtr hbm, int width, int height)
    {
        int bmiSize = Marshal.SizeOf<NativeMethods.BITMAPINFOHEADER>();
        IntPtr bmi = Marshal.AllocHGlobal(bmiSize + 16);
        IntPtr bits = Marshal.AllocHGlobal(width * height * 4);
        try
        {
            var header = new NativeMethods.BITMAPINFOHEADER
            {
                biSize = (uint)bmiSize,
                biWidth = width,
                biHeight = -height, // top-down
                biPlanes = 1,
                biBitCount = 32,
                biCompression = NativeMethods.BI_RGB,
                biSizeImage = (uint)(width * height * 4),
            };
            Marshal.StructureToPtr(header, bmi, false);

            int lines = NativeMethods.GetDIBits(hdc, hbm, 0, (uint)height, bits, bmi, NativeMethods.DIB_RGB_COLORS);
            if (lines == 0)
                throw new InvalidOperationException("GetDIBits failed, Win32 error " + Marshal.GetLastWin32Error());

            var bytes = new byte[width * height * 4];
            Marshal.Copy(bits, bytes, 0, bytes.Length);
            return new FrameImage(width, height, bytes, "gdi");
        }
        finally
        {
            Marshal.FreeHGlobal(bits);
            Marshal.FreeHGlobal(bmi);
        }
    }

    /// <summary>PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT|PW_CLIENTONLY).</summary>
    public static CaptureResult PrintWindow(WindowInfo w, uint flags = NativeMethods.PW_RENDERFULLCONTENT)
    {
        int width = w.Width, height = w.Height;
        IntPtr screenDc = NativeMethods.GetDC(IntPtr.Zero);
        IntPtr memDc = IntPtr.Zero, hbm = IntPtr.Zero, old = IntPtr.Zero;
        try
        {
            memDc = NativeMethods.CreateCompatibleDC(screenDc);
            hbm = NativeMethods.CreateCompatibleBitmap(screenDc, width, height);
            old = NativeMethods.SelectObject(memDc, hbm);

            bool ok = NativeMethods.PrintWindow(w.Handle, memDc, flags);
            if (!ok)
                return CaptureResult.Fail("printwindow", "PrintWindow returned FALSE, Win32 error " + Marshal.GetLastWin32Error());

            return new CaptureResult
            {
                Ok = true,
                Method = "printwindow",
                Image = ReadBitmap(memDc, hbm, width, height),
                Notes = $"flags=0x{flags:X}",
            };
        }
        catch (Exception ex)
        {
            return CaptureResult.Fail("printwindow", ex.Message);
        }
        finally
        {
            if (old != IntPtr.Zero) NativeMethods.SelectObject(memDc, old);
            if (hbm != IntPtr.Zero) NativeMethods.DeleteObject(hbm);
            if (memDc != IntPtr.Zero) NativeMethods.DeleteDC(memDc);
            NativeMethods.ReleaseDC(IntPtr.Zero, screenDc);
        }
    }

    /// <summary>BitBlt from the window's own DC (GetWindowDC).</summary>
    public static CaptureResult BitBltFromWindowDc(WindowInfo w)
    {
        int width = w.Width, height = w.Height;
        IntPtr winDc = IntPtr.Zero, memDc = IntPtr.Zero, hbm = IntPtr.Zero, old = IntPtr.Zero;
        try
        {
            winDc = NativeMethods.GetWindowDC(w.Handle);
            if (winDc == IntPtr.Zero)
                return CaptureResult.Fail("bitblt-window", "GetWindowDC failed, Win32 error " + Marshal.GetLastWin32Error());

            memDc = NativeMethods.CreateCompatibleDC(winDc);
            hbm = NativeMethods.CreateCompatibleBitmap(winDc, width, height);
            old = NativeMethods.SelectObject(memDc, hbm);

            bool ok = NativeMethods.BitBlt(memDc, 0, 0, width, height, winDc, 0, 0, NativeMethods.SRCCOPY);
            if (!ok)
                return CaptureResult.Fail("bitblt-window", "BitBlt failed, Win32 error " + Marshal.GetLastWin32Error());

            return new CaptureResult { Ok = true, Method = "bitblt-window", Image = ReadBitmap(memDc, hbm, width, height) };
        }
        catch (Exception ex)
        {
            return CaptureResult.Fail("bitblt-window", ex.Message);
        }
        finally
        {
            if (old != IntPtr.Zero) NativeMethods.SelectObject(memDc, old);
            if (hbm != IntPtr.Zero) NativeMethods.DeleteObject(hbm);
            if (memDc != IntPtr.Zero) NativeMethods.DeleteDC(memDc);
            if (winDc != IntPtr.Zero) NativeMethods.ReleaseDC(w.Handle, winDc);
        }
    }

    /// <summary>BitBlt straight off the screen DC: captures whatever is physically visible there.</summary>
    public static CaptureResult BitBltFromScreen(WindowInfo w)
    {
        int width = w.Width, height = w.Height;
        IntPtr screenDc = NativeMethods.GetDC(IntPtr.Zero);
        IntPtr memDc = IntPtr.Zero, hbm = IntPtr.Zero, old = IntPtr.Zero;
        try
        {
            memDc = NativeMethods.CreateCompatibleDC(screenDc);
            hbm = NativeMethods.CreateCompatibleBitmap(screenDc, width, height);
            old = NativeMethods.SelectObject(memDc, hbm);

            bool ok = NativeMethods.BitBlt(memDc, 0, 0, width, height, screenDc, w.X, w.Y,
                NativeMethods.SRCCOPY | NativeMethods.CAPTUREBLT);
            if (!ok)
                return CaptureResult.Fail("bitblt-screen", "BitBlt failed, Win32 error " + Marshal.GetLastWin32Error());

            return new CaptureResult { Ok = true, Method = "bitblt-screen", Image = ReadBitmap(memDc, hbm, width, height) };
        }
        catch (Exception ex)
        {
            return CaptureResult.Fail("bitblt-screen", ex.Message);
        }
        finally
        {
            if (old != IntPtr.Zero) NativeMethods.SelectObject(memDc, old);
            if (hbm != IntPtr.Zero) NativeMethods.DeleteObject(hbm);
            if (memDc != IntPtr.Zero) NativeMethods.DeleteDC(memDc);
            NativeMethods.ReleaseDC(IntPtr.Zero, screenDc);
        }
    }
}

/// <summary>
/// DWM thumbnail fallback: register the game window as a live thumbnail of a small helper window,
/// then read the helper window's pixels back off the screen.
/// Trade-offs: needs a visible on-screen helper window (so it flashes something on screen), only
/// captures at the helper's resolution, and is contaminated by anything drawn over the helper.
/// </summary>
public sealed class DwmThumbnailCapturer : IDisposable
{
    private static NativeMethods.WndProcDelegate s_wndProc = WndProc;
    private static NativeMethods.WNDCLASSEX? s_class;
    private static readonly object s_lock = new();
    private static string s_className;

    private IntPtr _hwnd;
    private IntPtr _thumb;
    private int _width, _height;

    public IntPtr HostWindow => _hwnd;

    public static DwmThumbnailCapturer Create(WindowInfo target, int x, int y, int width, int height, bool topmost = false)
    {
        var c = new DwmThumbnailCapturer { _width = width, _height = height };
        c.CreateHost(x, y, width, height, topmost);
        int hr = NativeMethods.DwmRegisterThumbnail(c._hwnd, target.Handle, out c._thumb);
        if (hr != 0) { c.Dispose(); Marshal.ThrowExceptionForHR(hr); }

        var props = new NativeMethods.DWM_THUMBNAIL_PROPERTIES
        {
            dwFlags = NativeMethods.DWM_TNP_RECTDESTINATION | NativeMethods.DWM_TNP_VISIBLE | NativeMethods.DWM_TNP_OPACITY,
            rcDestination = new NativeMethods.RECT { Left = 0, Top = 0, Right = width, Bottom = height },
            opacity = 255,
            fVisible = true,
            fSourceClientAreaOnly = false,
        };
        hr = NativeMethods.DwmUpdateThumbnailProperties(c._thumb, ref props);
        if (hr != 0) { c.Dispose(); Marshal.ThrowExceptionForHR(hr); }
        return c;
    }

    private void CreateHost(int x, int y, int width, int height, bool topmost)
    {
        lock (s_lock)
        {
            if (s_class == null)
            {
                s_className = "WinCapThumbHost_" + Guid.NewGuid().ToString("N")[..8];
                var wc = new NativeMethods.WNDCLASSEX
                {
                    cbSize = (uint)Marshal.SizeOf<NativeMethods.WNDCLASSEX>(),
                    style = 0,
                    lpfnWndProc = Marshal.GetFunctionPointerForDelegate(s_wndProc),
                    hInstance = NativeMethods.GetModuleHandleW(null),
                    hbrBackground = NativeMethods.CreateSolidBrush(0x00202020),
                    lpszClassName = s_className,
                };
                if (NativeMethods.RegisterClassExW(ref wc) == 0)
                    throw new InvalidOperationException("RegisterClassEx failed: " + Marshal.GetLastWin32Error());
                s_class = wc;
            }
        }

        uint exStyle = 0x00000080 /*WS_EX_TOOLWINDOW*/ | 0x08000000 /*WS_EX_NOACTIVATE*/;
        if (topmost) exStyle |= 0x00000008 /*WS_EX_TOPMOST*/;

        _hwnd = NativeMethods.CreateWindowExW(exStyle, s_className, "WinCapThumb", 0x80000000 /*WS_POPUP*/,
            x, y, width, height, IntPtr.Zero, IntPtr.Zero, NativeMethods.GetModuleHandleW(null), IntPtr.Zero);
        if (_hwnd == IntPtr.Zero)
            throw new InvalidOperationException("CreateWindowEx failed: " + Marshal.GetLastWin32Error());

        NativeMethods.ShowWindow(_hwnd, NativeMethods.SW_SHOWNOACTIVATE);
        NativeMethods.SetWindowPos(_hwnd, topmost ? new IntPtr(-1) : NativeMethods.HWND_TOP, x, y, width, height,
            NativeMethods.SWP_NOACTIVATE | NativeMethods.SWP_SHOWWINDOW);
    }

    private static IntPtr WndProc(IntPtr hwnd, uint msg, IntPtr wParam, IntPtr lParam)
        => NativeMethods.DefWindowProcW(hwnd, msg, wParam, lParam);

    /// <summary>Waits for DWM to compose the thumbnail into the host window, then reads screen pixels.</summary>
    public FrameImage Read(int settleMs = 250)
    {
        var start = Environment.TickCount64;
        while (Environment.TickCount64 - start < settleMs)
        {
            while (NativeMethods.PeekMessageW(out var msg, IntPtr.Zero, 0, 0, 1))
            {
                NativeMethods.TranslateMessage(ref msg);
                NativeMethods.DispatchMessageW(ref msg);
            }
            Thread.Sleep(15);
        }

        var screenDc = NativeMethods.GetDC(IntPtr.Zero);
        var memDc = IntPtr.Zero;
        var hbm = IntPtr.Zero;
        var old = IntPtr.Zero;
        try
        {
            memDc = NativeMethods.CreateCompatibleDC(screenDc);
            hbm = NativeMethods.CreateCompatibleBitmap(screenDc, _width, _height);
            old = NativeMethods.SelectObject(memDc, hbm);
            NativeMethods.GetWindowRect(_hwnd, out var wr);
            NativeMethods.BitBlt(memDc, 0, 0, _width, _height, screenDc, wr.Left, wr.Top, NativeMethods.SRCCOPY);
            return GdiCapture.ReadBitmap(memDc, hbm, _width, _height);
        }
        finally
        {
            if (old != IntPtr.Zero) NativeMethods.SelectObject(memDc, old);
            if (hbm != IntPtr.Zero) NativeMethods.DeleteObject(hbm);
            if (memDc != IntPtr.Zero) NativeMethods.DeleteDC(memDc);
            NativeMethods.ReleaseDC(IntPtr.Zero, screenDc);
        }
    }

    public void Dispose()
    {
        if (_thumb != IntPtr.Zero) { NativeMethods.DwmUnregisterThumbnail(_thumb); _thumb = IntPtr.Zero; }
        if (_hwnd != IntPtr.Zero) { NativeMethods.DestroyWindow(_hwnd); _hwnd = IntPtr.Zero; }
    }
}
