using System.Runtime.InteropServices;

namespace WinCap;

public enum CaptureMethod
{
    /// <summary>Windows.Graphics.Capture (recommended).</summary>
    Wgc,
    /// <summary>PrintWindow(PW_RENDERFULLCONTENT) - known to fail on many D3D windows.</summary>
    PrintWindow,
    /// <summary>BitBlt from the window DC.</summary>
    BitBltWindow,
    /// <summary>BitBlt from the screen DC at the window rect (requires unoccluded).</summary>
    BitBltScreen,
    /// <summary>DWM live thumbnail into a helper window, then screen read-back.</summary>
    DwmThumbnail,
}

public sealed class CaptureOptions
{
    public int TimeoutMs { get; set; } = 3000;
    public bool HideBorder { get; set; } = true;
    public bool IncludeCursor { get; set; }
    public PngWriter.Compression Compression { get; set; } = PngWriter.Compression.Fast;
    public bool ForceOpaque { get; set; } = true;
    /// <summary>Drop the window title bar / borders from the captured image (game client area only).</summary>
    public bool CropClient { get; set; }
    public int ThumbWidth { get; set; } = 300;
    public int ThumbHeight { get; set; } = 169;
    public int ThumbX { get; set; }
    public int ThumbY { get; set; }
    public bool ThumbTopmost { get; set; }
}

/// <summary>One-shot capture entry points used by both the CLI and library consumers.</summary>
public static class CaptureEngine
{
    public static CaptureResult Capture(WindowInfo w, CaptureMethod method, CaptureOptions options = null)
    {
        options ??= new CaptureOptions();
        var result = method switch
        {
            CaptureMethod.Wgc => CaptureWgc(w, options),
            CaptureMethod.PrintWindow => GdiCapture.PrintWindow(w),
            CaptureMethod.BitBltWindow => GdiCapture.BitBltFromWindowDc(w),
            CaptureMethod.BitBltScreen => GdiCapture.BitBltFromScreen(w),
            CaptureMethod.DwmThumbnail => CaptureDwmThumbnail(w, options),
            _ => throw new ArgumentOutOfRangeException(nameof(method)),
        };

        if (options.CropClient && result.Ok && result.Image != null && method != CaptureMethod.DwmThumbnail)
        {
            int ox = method == CaptureMethod.Wgc ? w.ClientCropX : w.ClientX - w.X;
            int oy = method == CaptureMethod.Wgc ? w.ClientCropY : w.ClientY - w.Y;
            if (ox >= 0 && oy >= 0 && ox + w.ClientWidth <= result.Image.Width && oy + w.ClientHeight <= result.Image.Height)
            {
                result = new CaptureResult
                {
                    Ok = true,
                    Method = result.Method,
                    Image = result.Image.Crop(ox, oy, w.ClientWidth, w.ClientHeight),
                    CaptureMs = result.CaptureMs,
                    Notes = result.Notes + $" croppedClient=({ox},{oy})",
                };
            }
        }
        return result;
    }

    private static CaptureResult CaptureWgc(WindowInfo w, CaptureOptions options)
    {
        var sw = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            using var cap = WgcWindowCapturer.Create(w.Handle, options.HideBorder, options.IncludeCursor);
            double openMs = sw.Elapsed.TotalMilliseconds;
            var image = cap.Grab(options.TimeoutMs);
            double grabMs = sw.Elapsed.TotalMilliseconds - openMs;
            sw.Stop();
            return new CaptureResult
            {
                Ok = true,
                Method = "wgc",
                Image = image,
                CaptureMs = sw.Elapsed.TotalMilliseconds,
                Notes = $"device={cap.DeviceKind} item={cap.ItemSize.Width}x{cap.ItemSize.Height} " +
                        $"api={cap.ItemApi} borderHidden={cap.BorderHidden} " +
                        $"openSession={openMs:F1}ms grab={grabMs:F1}ms" +
                        (cap.ItemError == null ? "" : $" interopError={cap.ItemError}"),
            };
        }
        catch (Exception ex)
        {
            return CaptureResult.Fail("wgc", ex.GetType().Name + ": " + ex.Message);
        }
    }

    private static CaptureResult CaptureDwmThumbnail(WindowInfo w, CaptureOptions options)
    {
        try
        {
            using var cap = DwmThumbnailCapturer.Create(w, options.ThumbX, options.ThumbY,
                options.ThumbWidth, options.ThumbHeight, options.ThumbTopmost);
            var image = cap.Read();
            return new CaptureResult
            {
                Ok = true,
                Method = "dwm-thumbnail",
                Image = image,
                Notes = $"host=({options.ThumbX},{options.ThumbY}) {options.ThumbWidth}x{options.ThumbHeight} " +
                        $"hostHwnd=0x{(long)cap.HostWindow:X}",
            };
        }
        catch (Exception ex)
        {
            return CaptureResult.Fail("dwm-thumbnail", ex.GetType().Name + ": " + ex.Message);
        }
    }

    /// <summary>Captures and writes a PNG in one call. Returns the result plus encode timing.</summary>
    public static CaptureResult CaptureToFile(WindowInfo w, CaptureMethod method, string outputPath, CaptureOptions options = null)
    {
        options ??= new CaptureOptions();
        var result = Capture(w, method, options);
        if (!result.Ok || result.Image == null) return result;

        var sw = System.Diagnostics.Stopwatch.StartNew();
        PngWriter.WriteBgra(outputPath, result.Image, options.ForceOpaque, options.Compression);
        sw.Stop();
        return new CaptureResult
        {
            Ok = true,
            Method = result.Method,
            Image = result.Image,
            CaptureMs = result.CaptureMs,
            EncodeMs = sw.Elapsed.TotalMilliseconds,
            Notes = result.Notes,
            Path = outputPath,
        };
    }
}

/// <summary>
/// Diagnostics-only helper: a plain opaque window used to occlude the target window during
/// occlusion tests. Never touches the target window itself.
/// </summary>
public sealed class OccluderWindow : IDisposable
{
    private static NativeMethods.WndProcDelegate s_wndProc = WndProc;
    private static readonly object s_lock = new();
    private static string s_className;

    private IntPtr _hwnd;
    private int _x, _y, _w, _h;

    public IntPtr Handle => _hwnd;

    public static OccluderWindow Create(int x, int y, int width, int height, uint bgrColor = 0x00303030, bool topmost = false)
    {
        lock (s_lock)
        {
            if (s_className == null)
            {
                s_className = "WinCapOccluder_" + Guid.NewGuid().ToString("N")[..8];
                var wc = new NativeMethods.WNDCLASSEX
                {
                    cbSize = (uint)Marshal.SizeOf<NativeMethods.WNDCLASSEX>(),
                    lpfnWndProc = Marshal.GetFunctionPointerForDelegate(s_wndProc),
                    hInstance = NativeMethods.GetModuleHandleW(null),
                    hbrBackground = NativeMethods.CreateSolidBrush(bgrColor),
                    lpszClassName = s_className,
                };
                if (NativeMethods.RegisterClassExW(ref wc) == 0)
                    throw new InvalidOperationException("RegisterClassEx failed: " + Marshal.GetLastWin32Error());
            }
        }

        uint exStyle = 0x00000080 /*WS_EX_TOOLWINDOW*/ | 0x08000000 /*WS_EX_NOACTIVATE*/;
        if (topmost) exStyle |= 0x00000008 /*WS_EX_TOPMOST*/;

        var w = new OccluderWindow { _x = x, _y = y, _w = width, _h = height };
        w._hwnd = NativeMethods.CreateWindowExW(exStyle, s_className, "WinCapOccluder",
            0x80000000 /*WS_POPUP*/ | 0x10000000 /*WS_VISIBLE*/,
            x, y, width, height, IntPtr.Zero, IntPtr.Zero, NativeMethods.GetModuleHandleW(null), IntPtr.Zero);
        if (w._hwnd == IntPtr.Zero)
            throw new InvalidOperationException("CreateWindowEx failed: " + Marshal.GetLastWin32Error());

        NativeMethods.ShowWindow(w._hwnd, NativeMethods.SW_SHOWNOACTIVATE);
        NativeMethods.SetWindowPos(w._hwnd, topmost ? new IntPtr(-1) : NativeMethods.HWND_TOP, x, y, width, height,
            NativeMethods.SWP_NOACTIVATE | NativeMethods.SWP_SHOWWINDOW);
        return w;
    }

    public void MoveTo(int x, int y, int width, int height)
    {
        _x = x; _y = y; _w = width; _h = height;
        NativeMethods.SetWindowPos(_hwnd, NativeMethods.HWND_TOP, x, y, width, height,
            NativeMethods.SWP_NOACTIVATE | NativeMethods.SWP_SHOWWINDOW);
    }

    public void Hide() => NativeMethods.ShowWindow(_hwnd, NativeMethods.SW_HIDE);

    public void Show() => NativeMethods.ShowWindow(_hwnd, NativeMethods.SW_SHOWNOACTIVATE);

    public void Activate() => NativeMethods.SetForegroundWindow(_hwnd);

    private static IntPtr WndProc(IntPtr hwnd, uint msg, IntPtr wParam, IntPtr lParam) => NativeMethods.DefWindowProcW(hwnd, msg, wParam, lParam);

    public void Dispose()
    {
        if (_hwnd != IntPtr.Zero) { NativeMethods.DestroyWindow(_hwnd); _hwnd = IntPtr.Zero; }
    }
}
