using System.Diagnostics;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using WinCap;

namespace WinCap.Cli;

internal static class Program
{
    private const string Version = "1.0.0";

    private static readonly JsonSerializerOptions JsonOpts = new() { WriteIndented = true };

    [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
    private static extern bool SetProcessDpiAwarenessContext(IntPtr value);

    private static int Main(string[] args)
    {
        Console.OutputEncoding = Encoding.UTF8;
        try { SetProcessDpiAwarenessContext(new IntPtr(-4)); } catch { }

        if (args.Length == 0) { PrintHelp(); return 2; }

        var a = new Args(args.Skip(1).ToArray());
        bool json = a.Flag("json");

        try
        {
            switch (args[0].ToLowerInvariant())
            {
                case "capture": return CmdCapture(a, json);
                case "info": return CmdInfo(a, json);
                case "methods": return CmdMethods(a, json);
                case "bench": return CmdBench(a, json);
                case "verify": return CmdVerify(a, json);
                case "selftest": return CmdSelftest(a, json);
                case "itemprobe": return CmdItemProbe(a, json);
                case "list": return ServeCommands.RunList(a, json);
                case "serve": return ServeCommands.RunServe(a);
                case "help":
                case "-h":
                case "--help":
                    PrintHelp(); return 0;
                default:
                    Console.Error.WriteLine($"Unknown command '{args[0]}'.");
                    PrintHelp();
                    return 2;
            }
        }
        catch (Exception ex)
        {
            if (json)
                Console.WriteLine(JsonSerializer.Serialize(new { ok = false, error = ex.GetType().Name + ": " + ex.Message }, JsonOpts));
            else
                Console.Error.WriteLine("ERROR: " + ex.GetType().Name + ": " + ex.Message);
            return 1;
        }
    }

    // ---------------------------------------------------------------- capture

    private static int CmdCapture(Args a, bool json)
    {
        var (w, targetFromPositional) = ResolveTarget(a);

        string outPath = a.Get("out");
        if (outPath == null)
        {
            int idx = targetFromPositional ? 1 : 0;
            if (a.Positional.Count > idx) outPath = a.Positional[idx];
        }
        if (string.IsNullOrWhiteSpace(outPath))
            throw new ArgumentException("no output path: pass --out <png> (or a positional path after the target)");
        outPath = Path.GetFullPath(outPath);

        var method = ParseMethod(a.Get("method", "wgc"));
        var opts = OptionsFrom(a);
        string format = (a.Get("format", "png") ?? "png").ToLowerInvariant();

        var total = Stopwatch.StartNew();
        var result = CaptureEngine.Capture(w, method, opts);
        if (!result.Ok)
        {
            Console.WriteLine(JsonSerializer.Serialize(new
            {
                ok = false,
                method = result.Method,
                error = result.Error,
                hwnd = $"0x{w.Hwnd:X}",
                pid = w.Pid,
            }));
            Console.Error.WriteLine($"FAILED [{result.Method}] {result.Error}");
            return 1;
        }

        var encode = Stopwatch.StartNew();
        if (format == "bmp") BmpWriter.WriteBgra(outPath, result.Image);
        else PngWriter.WriteBgra(outPath, result.Image, opts.ForceOpaque, opts.Compression);
        encode.Stop();
        double captureMs = result.CaptureMs;
        total.Stop();

        var stats = result.Image.Stats();
        string sha = Sha256File(outPath);
        var fi = new FileInfo(outPath);

        // stdout is always one compact JSON object - this is what the Python/SQT side parses.
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            ok = true,
            method = result.Method,
            path = outPath,
            hwnd = $"0x{w.Hwnd:X}",
            pid = w.Pid,
            width = result.Image.Width,
            height = result.Image.Height,
            clientArea = opts.CropClient,
            format,
            ms = Math.Round(total.Elapsed.TotalMilliseconds, 1),
            captureMs = Math.Round(captureMs, 1),
            encodeMs = Math.Round(encode.Elapsed.TotalMilliseconds, 1),
            fileBytes = fi.Length,
            sha256 = sha,
            looksBlank = stats.LooksBlank,
            meanRgb = new[] { Math.Round(stats.MeanR, 1), Math.Round(stats.MeanG, 1), Math.Round(stats.MeanB, 1) },
            notes = result.Notes,
        }));

        Console.Error.WriteLine($"OK {result.Method} {result.Image.Width}x{result.Image.Height} -> {outPath} " +
                                $"({total.Elapsed.TotalMilliseconds:F0} ms, capture {captureMs:F0} ms, encode {encode.Elapsed.TotalMilliseconds:F0} ms" +
                                $"{(stats.LooksBlank ? ", BLANK!" : "")})");
        return stats.LooksBlank ? 3 : 0;
    }

    /// <summary>Resolves --hwnd / --pid / --title / positional target.</summary>
    private static (WindowInfo window, bool fromPositional) ResolveTarget(Args a)
    {
        string hwnd = a.Get("hwnd");
        if (hwnd != null) return (WindowFinder.Resolve("hwnd:" + hwnd), false);

        string pid = a.Get("pid");
        if (pid != null) return (WindowFinder.Resolve("pid:" + pid), false);

        if (a.Positional.Count > 0 && !a.Positional[0].StartsWith("--", StringComparison.Ordinal))
            return (WindowFinder.Resolve(a.Positional[0], a.Get("title")), true);

        string title = a.Get("title");
        if (title != null) return (WindowFinder.Resolve(null, title), false);

        throw new ArgumentException("no target: pass --pid <pid>, --hwnd <hwnd>, --title <text>, or a positional pid/hwnd.");
    }

    // ---------------------------------------------------------------- info

    private static int CmdInfo(Args a, bool json)
    {
        var w = ResolveTarget(a).window;
        bool captureSupported;
        try { captureSupported = Windows.Graphics.Capture.GraphicsCaptureSession.IsSupported(); }
        catch { captureSupported = false; }

        var probe = new
        {
            center = WindowFinder.TopWindowAt(w.X + w.Width / 2, w.Y + w.Height / 2),
            topLeft = WindowFinder.TopWindowAt(w.X + 4, w.Y + 4),
            isOccluded = WindowFinder.IsOccluded(w),
        };

        if (json)
        {
            Console.WriteLine(JsonSerializer.Serialize(new
            {
                window = WindowDto(w),
                topAtCenter = new { hwnd = $"0x{probe.center.hwnd:X}", pid = probe.center.pid, title = probe.center.title },
                occluded = probe.isOccluded,
                wgcSupported = captureSupported,
                host = new
                {
                    os = Environment.OSVersion.VersionString,
                    runtime = Environment.Version.ToString(),
                    arch = System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture.ToString(),
                },
            }, JsonOpts));
            return 0;
        }

        Console.WriteLine($"hwnd         0x{w.Hwnd:X}   pid {w.Pid}   title \"{w.Title}\"");
        Console.WriteLine($"class        {w.ClassName}");
        Console.WriteLine($"window rect  ({w.X},{w.Y})  {w.Width}x{w.Height}");
        Console.WriteLine($"dwm frame    ({w.FrameX},{w.FrameY})  {w.FrameWidth}x{w.FrameHeight}   (this is what WGC captures)");
        Console.WriteLine($"client rect  ({w.ClientX},{w.ClientY})  {w.ClientWidth}x{w.ClientHeight}   cropInFrame=({w.ClientCropX},{w.ClientCropY})");
        Console.WriteLine($"chrome       left={w.ChromeLeft} top={w.ChromeTop} totalW={w.ChromeWidth} totalH={w.ChromeHeight}");
        Console.WriteLine($"dpi          {w.DpiAwarenessNote}");
        Console.WriteLine($"state        visible={w.IsVisible} minimized={w.IsMinimized} maximized={w.IsMaximized} cloaked={w.IsCloaked} foreground={w.IsForeground}");
        Console.WriteLine($"style        caption={w.HasCaption} thickframe={w.HasThickFrame} popup={w.IsPopup}");
        Console.WriteLine($"occluded     {probe.isOccluded}  (top window at centre: 0x{probe.center.hwnd:X} pid {probe.center.pid} \"{probe.center.title}\")");
        Console.WriteLine($"WGC support  GraphicsCaptureSession.IsSupported() = {captureSupported}");
        Console.WriteLine($"host         {Environment.OSVersion.VersionString}  .NET {Environment.Version}  " +
                          $"{System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture}  PerMonitorV2 (app.manifest)");
        return 0;
    }

    // ---------------------------------------------------------------- methods

    private static int CmdMethods(Args a, bool json)
    {
        var w = ResolveTarget(a).window;
        string dir = Path.GetFullPath(a.Get("out", "out"));
        Directory.CreateDirectory(dir);
        var opts = OptionsFrom(a);

        var order = new[]
        {
            CaptureMethod.Wgc, CaptureMethod.PrintWindow, CaptureMethod.BitBltWindow,
            CaptureMethod.BitBltScreen, CaptureMethod.DwmThumbnail,
        };

        var rows = new List<(string method, bool ok, string error, string path, int? width, int? height,
            double captureMs, double encodeMs, double totalMs, string notes, ImageStats stats)>();
        foreach (var m in order)
        {
            string name = MethodName(m);
            string path = Path.Combine(dir, $"{a.Get("prefix", "method")}-{name}.png");
            var sw = Stopwatch.StartNew();
            var r = CaptureEngine.CaptureToFile(w, m, path, opts);
            sw.Stop();
            var stats = r.Image?.Stats();
            rows.Add((name, r.Ok, r.Error, r.Ok ? path : null, r.Image?.Width, r.Image?.Height,
                Math.Round(r.CaptureMs, 1), Math.Round(r.EncodeMs, 1), Math.Round(sw.Elapsed.TotalMilliseconds, 1), r.Notes, stats));
        }

        if (json)
        {
            Console.WriteLine(JsonSerializer.Serialize(new
            {
                window = WindowDto(w),
                results = rows.Select(r => new
                {
                    method = r.method,
                    ok = r.ok,
                    error = r.error,
                    path = r.path,
                    width = r.width,
                    height = r.height,
                    captureMs = r.captureMs,
                    encodeMs = r.encodeMs,
                    totalMs = r.totalMs,
                    notes = r.notes,
                    stats = r.stats == null ? null : StatsDto(r.stats),
                    looksBlank = r.stats?.LooksBlank,
                }),
            }, JsonOpts));
        }
        else
        {
            Console.WriteLine($"target: {w}");
            Console.WriteLine();
            Console.WriteLine($"{"method",-15} {"ok",-3} {"size",-11} {"cap ms",-8} {"enc ms",-8} {"mean RGB",-18} {"white%",-8} verdict");
            foreach (var r in rows)
            {
                string size = r.width == null ? "-" : $"{r.width}x{r.height}";
                string mean = r.stats == null ? "-" : $"({r.stats.MeanR,5:F0},{r.stats.MeanG,5:F0},{r.stats.MeanB,5:F0})";
                string white = r.stats == null ? "-" : $"{r.stats.WhiteRatio * 100,6:F1}%";
                string verdict = !r.ok ? "FAIL: " + r.error : (r.stats.LooksBlank ? "BLANK" : "CONTENT");
                Console.WriteLine($"{r.method,-15} {(r.ok ? "yes" : "no"),-3} {size,-11} {r.captureMs,-8:F1} {r.encodeMs,-8:F1} {mean,-18} {white,-8} {verdict}");
            }
        }
        return 0;
    }

    // ---------------------------------------------------------------- bench

    private static int CmdBench(Args a, bool json)
    {
        var w = ResolveTarget(a).window;
        int warmIters = a.GetInt("iters", 20);
        string dir = Path.GetFullPath(a.Get("out", "out"));
        Directory.CreateDirectory(dir);
        string pngPath = Path.Combine(dir, "bench-warm.png");
        var opts = OptionsFrom(a);

        var proc = Process.GetCurrentProcess();
        double cpu0 = proc.TotalProcessorTime.TotalMilliseconds;
        double? gameCpu0 = TryGetProcessCpu(w.Pid);
        var wall = Stopwatch.StartNew();

        // ---- cold path: everything from scratch
        var cold = new List<double>();
        var openMs = new List<double>();
        var firstFrameMs = new List<double>();
        int coldIters = a.GetInt("cold-iters", 3);
        FrameImage reference = null;
        for (int i = 0; i < coldIters; i++)
        {
            var sw = Stopwatch.StartNew();
            using var cap = WgcWindowCapturer.Create(w.Handle, opts.HideBorder, opts.IncludeCursor);
            openMs.Add(sw.Elapsed.TotalMilliseconds);
            var img = cap.Grab(opts.TimeoutMs);
            double total = sw.Elapsed.TotalMilliseconds;
            cold.Add(total);
            firstFrameMs.Add(total - openMs[^1]);
            reference ??= img;
        }

        // ---- warm path: one session, repeated grabs
        var warmGrab = new List<double>();
        var warmFull = new List<double>();
        var encodeMs = new List<double>();
        using (var cap = WgcWindowCapturer.Create(w.Handle, opts.HideBorder, opts.IncludeCursor))
        {
            cap.Grab(opts.TimeoutMs); // prime
            for (int i = 0; i < warmIters; i++)
            {
                var sw = Stopwatch.StartNew();
                var img = cap.Grab(opts.TimeoutMs);
                double grab = sw.Elapsed.TotalMilliseconds;
                warmGrab.Add(grab);
                var sw2 = Stopwatch.StartNew();
                PngWriter.WriteBgra(pngPath, img, opts.ForceOpaque, opts.Compression);
                double enc = sw2.Elapsed.TotalMilliseconds;
                encodeMs.Add(enc);
                warmFull.Add(grab + enc);
                if (i == 0) Console.Error.WriteLine($"  (info) warm capture {img.Width}x{img.Height}, session {cap.DeviceKind}, item {cap.ItemSize.Width}x{cap.ItemSize.Height})");
            }
        }

        wall.Stop();
        proc.Refresh();
        double cpuMs = proc.TotalProcessorTime.TotalMilliseconds - cpu0;

        double? gameCpu1 = TryGetProcessCpu(w.Pid);
        double? gameDelta = gameCpu0.HasValue && gameCpu1.HasValue ? gameCpu1 - gameCpu0 : null;
        var result = new
        {
            window = WindowDto(w),
            iterations = new { cold = coldIters, warm = warmIters },
            cold = new { openSessionMs = Metric(openMs), firstFrameMs = Metric(firstFrameMs), totalMs = Metric(cold) },
            warm = new
            {
                grabMs = Metric(warmGrab),
                encodeAndWriteMs = Metric(encodeMs),
                endToEndMs = Metric(warmFull),
            },
            cost = new
            {
                wallMs = Math.Round(wall.Elapsed.TotalMilliseconds, 1),
                processCpuMs = Math.Round(cpuMs, 1),
                cpuPercentOfOneCore = Math.Round(cpuMs / wall.Elapsed.TotalMilliseconds * 100, 1),
                cpuPercentOfMachine = Math.Round(cpuMs / wall.Elapsed.TotalMilliseconds * 100 / Environment.ProcessorCount, 2),
                processors = Environment.ProcessorCount,
                gameProcessCpuDeltaMs = gameDelta,
            },
            outputPng = pngPath,
            outputPngBytes = new FileInfo(pngPath).Length,
        };

        if (json) Console.WriteLine(JsonSerializer.Serialize(result, JsonOpts));
        else
        {
            Console.WriteLine($"window: {w.Width}x{w.Height} client {w.ClientWidth}x{w.ClientHeight} dpi {w.Dpi}");
            Console.WriteLine($"cold  openSession  {Fmt(Metric(openMs))}");
            Console.WriteLine($"cold  firstFrame   {Fmt(Metric(firstFrameMs))}");
            Console.WriteLine($"cold  total        {Fmt(Metric(cold))}");
            Console.WriteLine($"warm  grab         {Fmt(Metric(warmGrab))}");
            Console.WriteLine($"warm  png encode   {Fmt(Metric(encodeMs))}");
            Console.WriteLine($"warm  end-to-end   {Fmt(Metric(warmFull))}");
            Console.WriteLine($"cost  wall {wall.Elapsed.TotalMilliseconds:F0} ms, process CPU {cpuMs:F0} ms " +
                              $"({cpuMs / wall.Elapsed.TotalMilliseconds * 100:F1}% of one core, {cpuMs / wall.Elapsed.TotalMilliseconds * 100 / Environment.ProcessorCount:F2}% of machine)");
            if (gameDelta != null) Console.WriteLine($"game  CPU used during bench {gameDelta:F0} ms of {wall.Elapsed.TotalMilliseconds:F0} ms wall (game keeps running; we never touch its message queue)");
            Console.WriteLine($"png   {new FileInfo(pngPath).Length / 1024.0:F1} KiB -> {pngPath}");
        }
        return 0;
    }

    private static double? TryGetProcessCpu(int pid)
    {
        try { return Process.GetProcessById(pid).TotalProcessorTime.TotalMilliseconds; }
        catch { return null; }
    }

    // ---------------------------------------------------------------- verify

    private static int CmdVerify(Args a, bool json)
    {
        if (a.Flag("self")) return CmdVerifySelf(a, json);

        var w = ResolveTarget(a).window;
        string dir = Path.GetFullPath(a.Get("out", "out"));
        Directory.CreateDirectory(dir);
        var opts = OptionsFrom(a);
        bool wasForeground = w.IsForeground;
        var steps = new List<object>();

        // 1. two consecutive baseline captures -> animation/noise floor
        var baseA = Snap("baseline-a", () => CaptureEngine.CaptureToFile(w, CaptureMethod.Wgc, Path.Combine(dir, "10-baseline-a.png"), opts), steps);
        Thread.Sleep(150);
        var baseB = Snap("baseline-b", () => CaptureEngine.CaptureToFile(w, CaptureMethod.Wgc, Path.Combine(dir, "10-baseline-b.png"), opts), steps);
        double noise = Diff(baseA, baseB);

        // 2. other methods against the (currently visible) window
        foreach (var (m, file) in new[]
                 {
                     (CaptureMethod.PrintWindow, "20-printwindow.png"),
                     (CaptureMethod.BitBltWindow, "21-bitblt-window.png"),
                     (CaptureMethod.BitBltScreen, "22-bitblt-screen.png"),
                 })
        {
            Snap(MethodName(m), () => CaptureEngine.CaptureToFile(w, m, Path.Combine(dir, file), opts), steps);
        }

        // 3. full occluder over the window
        bool occluded, foregroundDuringOcclusion;
        using (var occ = OccluderWindow.Create(w.X, w.Y, w.Width, w.Height, 0x00303030, a.Flag("topmost")))
        {
            Thread.Sleep(200);
            occluded = WindowFinder.IsOccluded(w);
            var covered = Snap("wgc-full-occluded", () => CaptureEngine.CaptureToFile(w, CaptureMethod.Wgc, Path.Combine(dir, "30-wgc-full-occluded.png"), opts), steps);
            var screenCovered = Snap("bitblt-screen-occluded", () => CaptureEngine.CaptureToFile(w, CaptureMethod.BitBltScreen, Path.Combine(dir, "31-bitblt-screen-occluded.png"), opts), steps);

            double dWgc = Diff(baseA, covered);
            double dScreen = Diff(baseA, screenCovered);

            // 4. left half covered
            occ.MoveTo(w.X, w.Y, w.Width / 2, w.Height);
            Thread.Sleep(200);
            var half = Snap("wgc-left-half-covered", () => CaptureEngine.CaptureToFile(w, CaptureMethod.Wgc, Path.Combine(dir, "40-wgc-left-half-covered.png"), opts), steps);
            double dLeft = HalfDiff(baseA, half, true);
            double dRight = HalfDiff(baseA, half, false);

            // 5. occluder takes foreground -> game is background AND occluded
            if (!a.Flag("no-activate")) occ.Activate();
            Thread.Sleep(250);
            foregroundDuringOcclusion = w.IsForeground;
            var bg = Snap("wgc-background-occluded", () => CaptureEngine.CaptureToFile(w, CaptureMethod.Wgc, Path.Combine(dir, "50-wgc-background-occluded.png"), opts), steps);
            double dBg = Diff(baseA, bg);

            steps.Add(new
            {
                step = "occlusion-diffs",
                occludedVisible = occluded,
                gameStillForegroundWhileOccluded = foregroundDuringOcclusion,
                baselineNoiseMeanAbsDiff = Round(noise),
                wgcFullOccludedVsBaseline = Round(dWgc),
                bitbltScreenOccludedVsBaseline = Round(dScreen),
                wgcLeftHalfCovered_leftVsBaseline = Round(dLeft),
                wgcLeftHalfCovered_rightVsBaseline = Round(dRight),
                wgcBackgroundOcccludedVsBaseline = Round(dBg),
            });
        }

        Thread.Sleep(150);
        var restored = Snap("after-occluder-removed", () => CaptureEngine.CaptureToFile(w, CaptureMethod.Wgc, Path.Combine(dir, "60-after-occluder-removed.png"), opts), steps);
        double dRestored = Diff(baseA, restored);

        bool foregroundRestored = false;
        if (wasForeground && !a.Flag("no-activate"))
        {
            foregroundRestored = WindowTools.TrySetForeground(w.Handle);
            Thread.Sleep(150);
        }

        var dto = new
        {
            window = WindowDto(w),
            steps,
            summary = new
            {
                baselineNoise = Round(noise),
                afterOccluderRemovedVsBaseline = Round(dRestored),
                gameWasForegroundAtStart = wasForeground,
                gameForegroundRestored = foregroundRestored,
            },
        };

        if (json) Console.WriteLine(JsonSerializer.Serialize(dto, JsonOpts));
        else
        {
            Console.WriteLine($"target: {w}");
            Console.WriteLine($"  baseline noise (2 consecutive WGC captures): {noise:F2} mean abs diff");
            foreach (dynamic s in steps) Console.WriteLine("  " + JsonSerializer.Serialize(s));
        }
        return 0;
    }

    /// <summary>
    /// Controlled occlusion proof that does not touch the user's windows: a test window of our own
    /// with a known static pattern is captured while visible, then while fully covered by a second
    /// window of ours, then with its pattern changed while still covered.
    /// </summary>
    private static int CmdVerifySelf(Args a, bool json)
    {
        string dir = Path.GetFullPath(a.Get("out", "out"));
        Directory.CreateDirectory(dir);
        var opts = OptionsFrom(a);
        int W = a.GetInt("w", 1000), H = a.GetInt("h", 180);
        int x = a.GetInt("x", 20), y = a.GetInt("y", 1225);
        var steps = new List<object>();

        using var target = TestTargetWindow.Create(x, y, W, H);
        Thread.Sleep(250);
        var targetInfo = WindowInfo.FromHandle(target.Handle);
        Console.Error.WriteLine($"  [diag] target hwnd=0x{targetInfo.Hwnd:X} visible={targetInfo.IsVisible} rect=({targetInfo.X},{targetInfo.Y}) {targetInfo.Width}x{targetInfo.Height} frame=({targetInfo.FrameX},{targetInfo.FrameY}) {targetInfo.FrameWidth}x{targetInfo.FrameHeight}");
        try { var it = CaptureInterop.CreateItemForWindow(target.Handle); Console.Error.WriteLine($"  [diag] CreateForWindow OK {it.Size.Width}x{it.Size.Height}"); }
        catch (Exception ex) { Console.Error.WriteLine($"  [diag] CreateForWindow ERR {ex.GetType().Name}: {ex.Message}"); }
        Console.Error.WriteLine($"  [diag] topWindowAtCentre=0x{WindowFinder.TopWindowAt(x + W / 2, y + H / 2).hwnd:X}");

        var a1 = Snap("self-visible", () => CaptureEngine.CaptureToFile(targetInfo, CaptureMethod.Wgc, Path.Combine(dir, "70-self-visible.png"), opts), steps);
        var screenVisible = Snap("self-visible-screen-bitblt", () => CaptureEngine.CaptureToFile(targetInfo, CaptureMethod.BitBltScreen, Path.Combine(dir, "71-self-visible-bitblt-screen.png"), opts), steps);

        // Fallback evaluation: DWM live thumbnail into a small helper window, then screen read-back.
        var thumbOpts = OptionsFrom(a);
        thumbOpts.ThumbX = 0;
        thumbOpts.ThumbY = 0;
        thumbOpts.ThumbWidth = 300;
        thumbOpts.ThumbHeight = 169;
        var dwm = Snap("self-dwm-thumbnail", () => CaptureEngine.CaptureToFile(targetInfo, CaptureMethod.DwmThumbnail, Path.Combine(dir, "76-self-dwm-thumbnail.png"), thumbOpts), steps);

        using var occ = OccluderWindow.Create(x - 10, y - 10, W + 20, H + 20);
        Thread.Sleep(250);
        bool covered = WindowFinder.TopWindowAt(x + W / 2, y + H / 2).hwnd == (long)occ.Handle;

        var a2 = Snap("self-covered", () => CaptureEngine.CaptureToFile(targetInfo, CaptureMethod.Wgc, Path.Combine(dir, "72-self-covered.png"), opts), steps);
        var screenCovered = Snap("self-covered-screen-bitblt", () => CaptureEngine.CaptureToFile(targetInfo, CaptureMethod.BitBltScreen, Path.Combine(dir, "73-self-covered-bitblt-screen.png"), opts), steps);

        double dCovered = Diff(a1, a2);
        double dScreen = Diff(a1, screenCovered);

        target.SetPhase(2);
        Thread.Sleep(150);
        var a3 = Snap("self-covered-new-content", () => CaptureEngine.CaptureToFile(targetInfo, CaptureMethod.Wgc, Path.Combine(dir, "74-self-covered-phase2.png"), opts), steps);
        double dLive = Diff(a2, a3);

        occ.MoveTo(x - 10, y - 10, W / 2 + 10, H + 20);
        Thread.Sleep(250);
        var a4 = Snap("self-left-half-covered", () => CaptureEngine.CaptureToFile(targetInfo, CaptureMethod.Wgc, Path.Combine(dir, "75-self-left-half-covered.png"), opts), steps);
        double dLeft = HalfDiff(a3, a4, true);
        double dRight = HalfDiff(a3, a4, false);

        bool passCovered = dCovered <= 1.0;
        bool passLive = dLive >= 5.0;
        bool passHalf = dLeft <= 1.0 && dRight <= 1.0;
        bool passScreenWrong = dScreen >= 20.0;

        var dto = new
        {
            target = WindowDto(targetInfo),
            geometry = new { x, y, w = W, h = H, coveredByOccluder = covered },
            steps,
            verdict = new
            {
                wgcWhileCoveredIdenticalToVisible = new { pass = passCovered, meanAbsDiff = Round(dCovered) },
                wgcTracksNewContentWhileCovered = new { pass = passLive, meanAbsDiff = Round(dLive) },
                wgcLeftHalfCoveredUnchanged = new { pass = passHalf, leftMeanAbsDiff = Round(dLeft), rightMeanAbsDiff = Round(dRight) },
                screenBitBltShowsOccluderNotWindow = new { pass = passScreenWrong, meanAbsDiff = Round(dScreen) },
                overall = passCovered && passLive && passHalf && passScreenWrong ? "PASS" : "FAIL",
            },
        };

        if (json) Console.WriteLine(JsonSerializer.Serialize(dto, JsonOpts));
        else
        {
            Console.WriteLine($"self-test target: {W}x{H} at ({x},{y}), covered by own occluder: {covered}");
            Console.WriteLine($"  WGC while covered vs visible      diff {dCovered:F2}  {(passCovered ? "PASS (identical)" : "FAIL")}");
            Console.WriteLine($"  WGC tracks new content while covered diff {dLive:F2}  {(passLive ? "PASS (live)" : "FAIL")}");
            Console.WriteLine($"  WGC half-covered unchanged         left {dLeft:F2} right {dRight:F2}  {(passHalf ? "PASS" : "FAIL")}");
            Console.WriteLine($"  screen BitBlt shows occluder       diff {dScreen:F2}  {(passScreenWrong ? "PASS (it does not see the window)" : "FAIL")}");
            Console.WriteLine($"  OVERALL {(passCovered && passLive && passHalf && passScreenWrong ? "PASS" : "FAIL")}");
        }
        return passCovered && passLive && passHalf ? 0 : 1;
    }

    private static double Diff(FrameImage a, FrameImage b)
    {
        if (a == null || b == null) return -1;
        if (a.Width == b.Width && a.Height == b.Height) return a.MeanAbsDiff(b);
        // e.g. WGC frame (1922x1112) vs window-rect BitBlt (1936x1119): compare the overlap.
        return a.MeanAbsDiffTopLeft(b, Math.Min(a.Width, b.Width), Math.Min(a.Height, b.Height));
    }
    private static double HalfDiff(FrameImage a, FrameImage b, bool left) => a == null || b == null ? -1 : left ? a.MeanAbsDiff(b, 0, 0, b.Width / 2, b.Height) : a.MeanAbsDiff(b, b.Width / 2, 0, b.Width, b.Height);

    private static FrameImage Snap(string name, Func<CaptureResult> capture, List<object> steps)
    {
        var sw = Stopwatch.StartNew();
        var r = capture();
        sw.Stop();
        var stats = r.Image?.Stats();
        if (!r.Ok) Console.Error.WriteLine($"  [step {name}] FAILED: {r.Error}");
        steps.Add(new
        {
            step = name,
            ok = r.Ok,
            error = r.Error,
            width = r.Image?.Width,
            height = r.Image?.Height,
            captureMs = Math.Round(r.CaptureMs, 1),
            totalMs = Math.Round(sw.Elapsed.TotalMilliseconds, 1),
            path = r.Path,
            notes = r.Notes,
            meanRgb = stats == null ? (object)null : new[] { Math.Round(stats.MeanR, 1), Math.Round(stats.MeanG, 1), Math.Round(stats.MeanB, 1) },
            whitePct = stats == null ? (double?)null : Math.Round(stats.WhiteRatio * 100, 2),
            blackPct = stats == null ? (double?)null : Math.Round(stats.BlackRatio * 100, 2),
            looksBlank = stats?.LooksBlank,
        });
        return r.Image;
    }

    // ---------------------------------------------------------------- itemprobe

    private static int CmdProbeSelfVariants(bool json)
    {
        var variants = new (string name, uint style, uint ex)[]
        {
            ("popup,noactivate", 0x90000000, 0x08000000),
            ("popup,plain", 0x90000000, 0x00000000),
            ("overlappedwindow", 0x10CF0000, 0x00000000),
            ("overlappedwindow,noactivate", 0x10CF0000, 0x08000000),
        };
        int y = 1240;
        foreach (var (name, style, ex) in variants)
        {
            using var t = TestTargetWindow.Create(20, y, 300, 100, style, ex);
            Thread.Sleep(250);
            string result;
            int iw = 0, ih = 0;
            try { var item = CaptureInterop.CreateItemForWindow(t.Handle); iw = item.Size.Width; ih = item.Size.Height; result = "OK"; }
            catch (Exception e) { result = e.GetType().Name + ": " + e.Message; }
            var info = WindowInfo.FromHandle(t.Handle);
            Console.WriteLine($"{name,-30} hwnd=0x{info.Hwnd:X} rect={info.Width}x{info.Height} visible={info.IsVisible} -> {(result == "OK" ? $"OK {iw}x{ih}" : result)}");
            y += 110;
            if (y > 1380) y = 1240;
        }
        return 0;
    }

    private static int CmdItemProbe(Args a, bool json)
    {
        if (a.Flag("self")) return CmdProbeSelfVariants(json);

        var w = ResolveTarget(a).window;
        string interop = "ok", windowId = "ok";
        int iw = 0, ih = 0, ww = 0, wh = 0;
        try
        {
            var item = CaptureInterop.CreateItemForWindow(w.Handle);
            iw = item.Size.Width; ih = item.Size.Height;
        }
        catch (Exception ex) { interop = ex.ToString(); }

        try
        {
            var item = CaptureInterop.TryCreateItemFromWindowId(w.Handle);
            if (item == null) windowId = "null";
            else { ww = item.Size.Width; wh = item.Size.Height; }
        }
        catch (Exception ex) { windowId = ex.ToString(); }

        if (json)
            Console.WriteLine(JsonSerializer.Serialize(new { hwnd = $"0x{w.Hwnd:X}", interop, interopSize = $"{iw}x{ih}", windowId, windowIdSize = $"{ww}x{wh}" }, JsonOpts));
        else
        {
            Console.WriteLine($"hwnd 0x{w.Hwnd:X}");
            Console.WriteLine($"CreateForWindow          -> {(interop == "ok" ? $"OK {iw}x{ih}" : interop)}");
            Console.WriteLine($"TryCreateFromWindowId    -> {(windowId == "ok" ? $"OK {ww}x{wh}" : windowId)}");
        }
        return 0;
    }

    // ---------------------------------------------------------------- selftest

    private static int CmdSelftest(Args a, bool json)
    {
        bool supported;
        string supportError = null;
        try { supported = Windows.Graphics.Capture.GraphicsCaptureSession.IsSupported(); }
        catch (Exception ex) { supported = false; supportError = ex.Message; }

        var dto = new
        {
            version = Version,
            os = Environment.OSVersion.VersionString,
            build = Environment.OSVersion.Version.Build,
            runtime = Environment.Version.ToString(),
            arch = System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture.ToString(),
            wgcSupported = supported,
            wgcError = supportError,
        };

        if (json) { Console.WriteLine(JsonSerializer.Serialize(dto, JsonOpts)); return 0; }
        Console.WriteLine($"wincap {Version}");
        Console.WriteLine($"  OS            {dto.os} (build {dto.build})");
        Console.WriteLine($"  .NET runtime  {dto.runtime}  ({dto.arch})");
        Console.WriteLine($"  DPI awareness PerMonitorV2 (app.manifest)");
        Console.WriteLine($"  WGC supported {dto.wgcSupported}" + (supportError == null ? "" : " (" + supportError + ")"));
        return 0;
    }

    // ---------------------------------------------------------------- helpers

    private static CaptureOptions OptionsFrom(Args a) => new()
    {
        TimeoutMs = a.GetInt("timeout", 3000),
        HideBorder = !a.Has("no-hide-border"),
        IncludeCursor = a.Flag("cursor"),
        Compression = a.Get("png", "fast").ToLowerInvariant() switch
        {
            "best" => PngWriter.Compression.Best,
            "store" or "none" => PngWriter.Compression.Store,
            _ => PngWriter.Compression.Fast,
        },
        ForceOpaque = !a.Flag("keep-alpha"),
        CropClient = a.Flag("crop-client") || a.Flag("client-area"),
        ThumbWidth = a.GetInt("thumb-w", 300),
        ThumbHeight = a.GetInt("thumb-h", 169),
        ThumbX = a.GetInt("thumb-x", 0),
        ThumbY = a.GetInt("thumb-y", 0),
        ThumbTopmost = a.Flag("thumb-topmost"),
    };

    private static CaptureMethod ParseMethod(string s) => s.ToLowerInvariant() switch
    {
        "wgc" or "window-graphics-capture" => CaptureMethod.Wgc,
        "printwindow" or "print-window" => CaptureMethod.PrintWindow,
        "bitblt-window" or "bitblt" => CaptureMethod.BitBltWindow,
        "bitblt-screen" or "screen" => CaptureMethod.BitBltScreen,
        "dwm-thumbnail" or "dwm" or "thumbnail" => CaptureMethod.DwmThumbnail,
        _ => throw new ArgumentException($"Unknown method '{s}' (wgc|printwindow|bitblt-window|bitblt-screen|dwm-thumbnail)."),
    };

    private static string MethodName(CaptureMethod m) => m switch
    {
        CaptureMethod.Wgc => "wgc",
        CaptureMethod.PrintWindow => "printwindow",
        CaptureMethod.BitBltWindow => "bitblt-window",
        CaptureMethod.BitBltScreen => "bitblt-screen",
        CaptureMethod.DwmThumbnail => "dwm-thumbnail",
        _ => m.ToString(),
    };

    private static object StatsDto(ImageStats s) => new
    {
        meanR = Math.Round(s.MeanR, 1),
        meanG = Math.Round(s.MeanG, 1),
        meanB = Math.Round(s.MeanB, 1),
        whitePct = Math.Round(s.WhiteRatio * 100, 2),
        blackPct = Math.Round(s.BlackRatio * 100, 2),
        uniqueColorRatio = Math.Round(s.UniqueColorRatio, 4),
        sampled = s.SampledPixels,
        looksBlank = s.LooksBlank,
    };

    private static object WindowDto(WindowInfo w) => new
    {
        hwnd = $"0x{w.Hwnd:X}",
        hwndDecimal = w.Hwnd,
        pid = w.Pid,
        title = w.Title,
        @class = w.ClassName,
        rect = new { x = w.X, y = w.Y, w = w.Width, h = w.Height },
        client = new { x = w.ClientX, y = w.ClientY, w = w.ClientWidth, h = w.ClientHeight },
        chrome = new { left = w.ChromeLeft, top = w.ChromeTop, w = w.ChromeWidth, h = w.ChromeHeight },
        dpi = w.Dpi,
        visible = w.IsVisible,
        minimized = w.IsMinimized,
        cloaked = w.IsCloaked,
        foreground = w.IsForeground,
        occluded = WindowFinder.IsOccluded(w),
    };

    private static string Sha256File(string path)
    {
        using var sha = SHA256.Create();
        using var fs = File.OpenRead(path);
        return Convert.ToHexString(sha.ComputeHash(fs)).ToLowerInvariant();
    }

    private static object Metric(List<double> values)
    {
        var s = values.OrderBy(v => v).ToArray();
        if (s.Length == 0) return new { min = 0.0, median = 0.0, p90 = 0.0, max = 0.0, mean = 0.0, n = 0 };
        return new
        {
            min = Math.Round(s[0], 2),
            median = Math.Round(s[s.Length / 2], 2),
            p90 = Math.Round(s[Math.Min(s.Length - 1, (int)(s.Length * 0.9))], 2),
            max = Math.Round(s[^1], 2),
            mean = Math.Round(s.Average(), 2),
            n = s.Length,
        };
    }

    private static string Fmt(object metric)
    {
        var t = metric.GetType();
        double med = (double)t.GetProperty("median").GetValue(metric);
        double p90 = (double)t.GetProperty("p90").GetValue(metric);
        double min = (double)t.GetProperty("min").GetValue(metric);
        double max = (double)t.GetProperty("max").GetValue(metric);
        int n = (int)t.GetProperty("n").GetValue(metric);
        return $"median {med,7:F1} ms   p90 {p90,7:F1}   min {min,6:F1}   max {max,7:F1}   (n={n})";
    }

    private static double Round(double v) => double.IsNaN(v) ? -1 : Math.Round(v, 3);

    private static void PrintHelp()
    {
        Console.WriteLine($"""
        wincap {Version} - window capture for occluded / backgrounded Windows apps (WGC first).

        USAGE
          wincap capture <pid-or-hwnd> <output.png> [options]   capture one frame to PNG
          wincap info <pid-or-hwnd> [--json]                    window + capture-eligibility details
          wincap methods <pid-or-hwnd> [--out DIR]              run every method, compare results
          wincap bench <pid-or-hwnd> [--iters N] [--out DIR]    cold/warm latency + CPU measurement
          wincap verify <pid-or-hwnd> [--out DIR]               occlusion / overlap proof harness
          wincap selftest [--json]                              environment check

        TARGET
          8400 | pid:8400 | 0x50638 | hwnd:0x50638 | --title 明日方舟

        OPTIONS
          --method wgc|printwindow|bitblt-window|bitblt-screen|dwm-thumbnail   (default: wgc)
          --timeout <ms>        frame wait timeout (default 3000)
          --no-hide-border      keep the Windows 11 yellow capture border
          --cursor              include the mouse cursor
          --png fast|best|store PNG compression (default fast)
          --json                machine readable output on stdout
          --out DIR             output directory for methods/bench/verify
          --thumb-w/--thumb-h/--thumb-x/--thumb-y/--thumb-topmost   DWM thumbnail host geometry

        EXIT CODES  0 ok, 1 capture failed, 2 usage, 3 captured but image looks blank
        """);
    }
}
