using System.Runtime.InteropServices;
using System.Text;

namespace WinCap;

/// <summary>Snapshot of a top-level window's identity and geometry (physical pixels).</summary>
public sealed class WindowInfo
{
    public long Hwnd { get; init; }
    public int Pid { get; init; }
    public string Title { get; init; } = "";
    public string ClassName { get; init; } = "Unknown";

    public int X { get; init; }
    public int Y { get; init; }
    public int Width { get; init; }
    public int Height { get; init; }

    public int ClientX { get; init; }
    public int ClientY { get; init; }
    public int ClientWidth { get; init; }
    public int ClientHeight { get; init; }

    public int Dpi { get; init; }
    public bool IsVisible { get; init; }
    public bool IsMinimized { get; init; }
    public bool IsMaximized { get; init; }
    public bool IsCloaked { get; init; }
    public bool IsForeground { get; init; }
    public bool HasCaption { get; init; }
    public bool HasThickFrame { get; init; }
    public bool IsPopup { get; init; }

    /// <summary>DWMWA_EXTENDED_FRAME_BOUNDS: the visible frame, i.e. exactly what WGC captures.</summary>
    public int FrameX { get; init; }
    public int FrameY { get; init; }
    public int FrameWidth { get; init; }
    public int FrameHeight { get; init; }
    public bool HasFrameBounds { get; init; }

    public IntPtr Handle => (IntPtr)Hwnd;

    /// <summary>Window chrome included in the window rectangle vs. the client area.</summary>
    public int ChromeLeft => ClientX - X;
    public int ChromeTop => ClientY - Y;
    public int ChromeWidth => Width - ClientWidth;
    public int ChromeHeight => Height - ClientHeight;

    /// <summary>Client-area offset inside the WGC frame (extended frame bounds origin).</summary>
    public int ClientCropX => HasFrameBounds ? ClientX - FrameX : ClientX - X;
    public int ClientCropY => HasFrameBounds ? ClientY - FrameY : ClientY - Y;

    public string DpiAwarenessNote => Dpi == 96 ? "96 DPI (100%)" : $"{Dpi} DPI ({Dpi * 100 / 96}%)";

    public static WindowInfo FromHandle(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero || !NativeMethods.IsWindow(hwnd))
            throw new ArgumentException($"0x{(long)hwnd:X} is not a valid window handle.");

        NativeMethods.GetWindowThreadProcessId(hwnd, out uint pid);
        var title = new StringBuilder(512);
        NativeMethods.GetWindowTextW(hwnd, title, title.Capacity);
        var cls = new StringBuilder(256);
        NativeMethods.GetClassNameW(hwnd, cls, cls.Capacity);

        NativeMethods.GetWindowRect(hwnd, out var wr);
        NativeMethods.GetClientRect(hwnd, out var cr);
        var origin = new NativeMethods.POINT();
        NativeMethods.ClientToScreen(hwnd, ref origin);

        NativeMethods.DwmGetWindowAttribute(hwnd, NativeMethods.DWMWA_CLOAKED, out int cloaked, sizeof(int));
        int style = NativeMethods.GetWindowLongW(hwnd, NativeMethods.GWL_STYLE);

        bool hasFrame = NativeMethods.DwmGetWindowAttribute(hwnd, NativeMethods.DWMWA_EXTENDED_FRAME_BOUNDS,
            out NativeMethods.RECT frameRect, Marshal.SizeOf<NativeMethods.RECT>()) == 0 && frameRect.Right > frameRect.Left;

        return new WindowInfo
        {
            Hwnd = hwnd.ToInt64(),
            Pid = (int)pid,
            Title = title.ToString(),
            ClassName = cls.ToString(),
            X = wr.Left,
            Y = wr.Top,
            Width = wr.Width,
            Height = wr.Height,
            ClientX = origin.X,
            ClientY = origin.Y,
            ClientWidth = cr.Width,
            ClientHeight = cr.Height,
            Dpi = (int)NativeMethods.GetDpiForWindow(hwnd),
            IsVisible = NativeMethods.IsWindowVisible(hwnd),
            IsMinimized = NativeMethods.IsIconic(hwnd),
            IsMaximized = NativeMethods.IsZoomed(hwnd),
            IsCloaked = cloaked != 0,
            IsForeground = NativeMethods.GetForegroundWindow() == hwnd,
            HasCaption = (style & NativeMethods.WS_CAPTION) != 0,
            HasThickFrame = (style & NativeMethods.WS_THICKFRAME) != 0,
            IsPopup = (style & NativeMethods.WS_POPUP) != 0,
            HasFrameBounds = hasFrame,
            FrameX = hasFrame ? frameRect.Left : wr.Left,
            FrameY = hasFrame ? frameRect.Top : wr.Top,
            FrameWidth = hasFrame ? frameRect.Width : wr.Width,
            FrameHeight = hasFrame ? frameRect.Height : wr.Height,
        };
    }

    public override string ToString() =>
        $"hwnd=0x{Hwnd:X} pid={Pid} \"{Title}\" window=({X},{Y}) {Width}x{Height} " +
        $"client=({ClientX},{ClientY}) {ClientWidth}x{ClientHeight} dpi={Dpi} " +
        $"visible={IsVisible} minimized={IsMinimized} cloaked={IsCloaked} foreground={IsForeground}";
}

/// <summary>Resolves a "pid or hwnd" target string into a concrete top-level window.</summary>
public static class WindowFinder
{
    /// <summary>
    /// Accepts "hwnd:0x50638", "pid:8400", "0x50638", "8400" (PID preferred, HWND fallback),
    /// or a decimal HWND. Also supports "--title substring" style lookup through <paramref name="titleSubstring"/>.
    /// </summary>
    public static WindowInfo Resolve(string target, string titleSubstring = null)
    {
        if (!string.IsNullOrWhiteSpace(target))
        {
            string t = target.Trim();
            string kind = null;
            int colon = t.IndexOf(':');
            if (colon > 0)
            {
                string prefix = t[..colon].ToLowerInvariant();
                if (prefix is "hwnd" or "pid") { kind = prefix; t = t[(colon + 1)..].Trim(); }
            }

            if (kind == "hwnd" || (kind == null && t.StartsWith("0x", StringComparison.OrdinalIgnoreCase)))
                return WindowInfo.FromHandle(ParseHwnd(t));

            if (kind == "pid")
                return FromPid(int.Parse(t));

            if (int.TryParse(t, out int numeric))
            {
                try
                {
                    if (System.Diagnostics.Process.GetProcessById(numeric) is { } p)
                        return FromPid(numeric);
                }
                catch (ArgumentException) { /* no such process -> treat as hwnd */ }
                if (NativeMethods.IsWindow((IntPtr)numeric))
                    return WindowInfo.FromHandle((IntPtr)numeric);
                throw new ArgumentException($"No process and no window with id {numeric}.");
            }
            throw new ArgumentException($"Unrecognised target '{target}'.");
        }

        if (titleSubstring != null)
        {
            foreach (var w in Enumerate())
                if (w.Title.Contains(titleSubstring, StringComparison.OrdinalIgnoreCase)) return w;
            throw new ArgumentException($"No window title contains '{titleSubstring}'.");
        }

        throw new ArgumentException("No target specified (pass a PID or HWND).");
    }

    public static IntPtr ParseHwnd(string s)
    {
        s = s.Trim();
        long v = s.StartsWith("0x", StringComparison.OrdinalIgnoreCase)
            ? long.Parse(s[2..], System.Globalization.NumberStyles.HexNumber)
            : long.Parse(s);
        return (IntPtr)v;
    }

    /// <summary>Picks the best top-level window of a process: largest visible, titled, non-tool window.</summary>
    public static WindowInfo FromPid(int pid)
    {
        WindowInfo best = null;
        double bestScore = double.MinValue;
        foreach (var w in Enumerate())
        {
            if (w.Pid != pid) continue;
            if (!w.IsVisible) continue;
            if (w.Width <= 0 || w.Height <= 0) continue;
            if (w.IsCloaked) continue;

            double score = (double)w.Width * w.Height;
            if (w.HasCaption) score *= 1.5;
            if (w.IsMinimized) score *= 0.01;
            if (score > bestScore) { bestScore = score; best = w; }
        }
        if (best == null)
            throw new ArgumentException($"Process {pid} has no visible top-level window.");
        return best;
    }

    public static IEnumerable<WindowInfo> Enumerate()
    {
        var handles = new List<IntPtr>(256);
        NativeMethods.EnumWindows((h, _) =>
        {
            if (NativeMethods.IsWindowVisible(h)) handles.Add(h);
            return true;
        }, IntPtr.Zero);

        foreach (var h in handles)
        {
            WindowInfo w;
            try { w = WindowInfo.FromHandle(h); }
            catch { continue; }
            yield return w;
        }
    }

    /// <summary>Which window currently owns the screen pixels at a point (for occlusion evidence).</summary>
    public static (long hwnd, int pid, string title) TopWindowAt(int x, int y)
    {
        var p = new NativeMethods.POINT { X = x, Y = y };
        IntPtr h = NativeMethods.WindowFromPoint(p);
        if (h == IntPtr.Zero) return (0, 0, "");
        IntPtr root = NativeMethods.GetAncestor(h, 2 /*GA_ROOT*/);
        if (root != IntPtr.Zero) h = root;
        NativeMethods.GetWindowThreadProcessId(h, out uint pid);
        var sb = new StringBuilder(256);
        NativeMethods.GetWindowTextW(h, sb, sb.Capacity);
        return (h.ToInt64(), (int)pid, sb.ToString());
    }

    /// <summary>True when some other window covers the centre of the target (i.e. target is occluded).</summary>
    public static bool IsOccluded(WindowInfo w)
    {
        var (hwnd, _, _) = TopWindowAt(w.X + w.Width / 2, w.Y + w.Height / 2);
        return hwnd != w.Hwnd;
    }
}
