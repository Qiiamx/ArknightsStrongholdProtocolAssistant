using System.Diagnostics;
using System.Text;
using System.Text.Json;
using WinCap;

namespace WinCap.Cli;

/// <summary>
/// Resident ("serve") mode and window listing. Serve keeps WGC sessions open so a capture is
/// ~1 GPU readback + PNG encode instead of a full device/session setup.
/// Protocol: one JSON object per line on stdin, one JSON object per line on stdout.
/// </summary>
internal static class ServeCommands
{
    private static readonly JsonSerializerOptions Compact = new() { WriteIndented = false };

    public static int RunList(Args a, bool json)
    {
        var rows = new List<WindowInfo>();
        int filterPid = a.GetInt("pid", -1);
        string title = a.Get("title");

        foreach (var w in WindowFinder.Enumerate())
        {
            if (filterPid > 0 && w.Pid != filterPid) continue;
            if (title != null && !w.Title.Contains(title, StringComparison.OrdinalIgnoreCase)) continue;
            if (!a.Flag("all"))
            {
                if (string.IsNullOrWhiteSpace(w.Title)) continue;
                if (w.Width < 64 || w.Height < 64) continue;
            }
            rows.Add(w);
        }

        var ordered = rows.OrderByDescending(w => (long)w.Width * w.Height).ToList();

        if (json)
        {
            Console.WriteLine(JsonSerializer.Serialize(new
            {
                count = ordered.Count,
                windows = ordered.Select(w => new
                {
                    hwnd = $"0x{w.Hwnd:X}",
                    pid = w.Pid,
                    title = w.Title,
                    @class = w.ClassName,
                    rect = new { x = w.X, y = w.Y, w = w.Width, h = w.Height },
                    client = new { x = w.ClientX, y = w.ClientY, w = w.ClientWidth, h = w.ClientHeight },
                    dpi = w.Dpi,
                    visible = w.IsVisible,
                    minimized = w.IsMinimized,
                    foreground = w.IsForeground,
                    occluded = WindowFinder.IsOccluded(w),
                }),
            }, new JsonSerializerOptions { WriteIndented = true }));
            return 0;
        }

        Console.WriteLine($"{"pid",-7} {"hwnd",-10} {"client",-12} {"window",-12} {"dpi",-4} {"vis",-4} {"min",-4} {"fg",-3} {"occ",-4} title");
        foreach (var w in ordered)
        {
            string hw = ("0x" + w.Hwnd.ToString("X8")).PadRight(10);
            Console.WriteLine($"{w.Pid,-7} {hw} {w.ClientWidth + "x" + w.ClientHeight,-12} " +
                              $"{w.Width + "x" + w.Height,-12} {w.Dpi,-4} {(w.IsVisible ? "y" : "n"),-4} " +
                              $"{(w.IsMinimized ? "y" : "n"),-4} {(w.IsForeground ? "y" : "n"),-3} " +
                              $"{(WindowFinder.IsOccluded(w) ? "y" : "n"),-4} {Trunc(w.Title, 60)}");
        }
        Console.WriteLine($"{ordered.Count} window(s)");
        return 0;
    }

    private static string Trunc(string s, int n) => s.Length <= n ? s : s[..(n - 1)] + "...";

    public static int RunServe(Args a)
    {
        bool defaultCrop = a.Flag("client-area") || a.Flag("crop-client");
        var compression = (a.Get("png", "fast") ?? "fast").ToLowerInvariant() switch
        {
            "best" => PngWriter.Compression.Best,
            "store" or "none" => PngWriter.Compression.Store,
            _ => PngWriter.Compression.Fast,
        };
        int defaultTimeout = a.GetInt("timeout", 2000);

        var cache = new Dictionary<long, WgcWindowCapturer>();
        var stdin = new StreamReader(Console.OpenStandardInput(), new UTF8Encoding(false));
        var stdout = Console.Out;

        stdout.WriteLine(JsonSerializer.Serialize(new { ok = true, ready = true, mode = "serve", pid = Environment.ProcessId }));
        stdout.Flush();

        string line;
        while ((line = stdin.ReadLine()) != null)
        {
            line = line.Trim();
            if (line.Length == 0) continue;

            string response;
            bool quit = false;
            try
            {
                using var doc = JsonDocument.Parse(line);
                var root = doc.RootElement;
                string cmd = root.TryGetProperty("cmd", out var c) ? c.GetString()!.ToLowerInvariant() : "capture";

                switch (cmd)
                {
                    case "quit":
                        quit = true;
                        response = "{\"ok\":true,\"quit\":true}";
                        break;
                    case "ping":
                        response = JsonSerializer.Serialize(new { ok = true, pong = true });
                        break;
                    case "release":
                        foreach (var s in cache.Values) { try { s.Dispose(); } catch { } }
                        cache.Clear();
                        response = JsonSerializer.Serialize(new { ok = true, released = true });
                        break;
                    case "list":
                        response = ListJson();
                        break;
                    case "info":
                        response = InfoJson(root);
                        break;
                    default:
                        response = CaptureJson(root, cache, defaultCrop, compression, defaultTimeout);
                        break;
                }
            }
            catch (Exception ex)
            {
                response = JsonSerializer.Serialize(new { ok = false, error = ex.GetType().Name + ": " + ex.Message }, Compact);
            }

            stdout.WriteLine(response);
            stdout.Flush();
            if (quit) break;
        }

        foreach (var s in cache.Values) { try { s.Dispose(); } catch { } }
        return 0;
    }

    private static string ListJson()
    {
        var wins = WindowFinder.Enumerate()
            .Where(w => !string.IsNullOrWhiteSpace(w.Title) && w.Width >= 64 && w.Height >= 64)
            .OrderByDescending(w => (long)w.Width * w.Height)
            .Select(w => new
            {
                hwnd = $"0x{w.Hwnd:X}",
                pid = w.Pid,
                title = w.Title,
                client = new { w = w.ClientWidth, h = w.ClientHeight },
                minimized = w.IsMinimized,
                foreground = w.IsForeground,
            });
        return JsonSerializer.Serialize(new { ok = true, windows = wins }, Compact);
    }

    private static string InfoJson(JsonElement root)
    {
        var w = ResolveWindow(root);
        return JsonSerializer.Serialize(new
        {
            ok = true,
            hwnd = $"0x{w.Hwnd:X}",
            pid = w.Pid,
            title = w.Title,
            rect = new { x = w.X, y = w.Y, w = w.Width, h = w.Height },
            frame = new { x = w.FrameX, y = w.FrameY, w = w.FrameWidth, h = w.FrameHeight },
            client = new { x = w.ClientX, y = w.ClientY, w = w.ClientWidth, h = w.ClientHeight },
            clientCropInFrame = new { x = w.ClientCropX, y = w.ClientCropY },
            dpi = w.Dpi,
            scale = Math.Round(w.Dpi / 96.0, 2),
            minimized = w.IsMinimized,
            foreground = w.IsForeground,
            occluded = WindowFinder.IsOccluded(w),
        }, Compact);
    }

    private static WindowInfo ResolveWindow(JsonElement root)
    {
        if (root.TryGetProperty("hwnd", out var h) && h.ValueKind != JsonValueKind.Null)
        {
            string s = h.ValueKind == JsonValueKind.String ? h.GetString() : h.GetRawText();
            return WindowFinder.Resolve("hwnd:" + s);
        }
        if (root.TryGetProperty("pid", out var p) && p.ValueKind != JsonValueKind.Null)
            return WindowFinder.Resolve("pid:" + (p.ValueKind == JsonValueKind.String ? p.GetString() : p.GetRawText()));
        if (root.TryGetProperty("title", out var t) && t.ValueKind == JsonValueKind.String)
            return WindowFinder.Resolve(null, t.GetString());
        throw new ArgumentException("request needs hwnd, pid or title");
    }

    private static string CaptureJson(JsonElement root, Dictionary<long, WgcWindowCapturer> cache,
        bool defaultCrop, PngWriter.Compression compression, int defaultTimeout)
    {
        var sw = Stopwatch.StartNew();
        var w = ResolveWindow(root);

        bool crop = defaultCrop;
        if (root.TryGetProperty("clientArea", out var ca) && ca.ValueKind is JsonValueKind.True or JsonValueKind.False)
            crop = ca.GetBoolean();

        int timeout = root.TryGetProperty("timeoutMs", out var to) && to.TryGetInt32(out int t) ? t : defaultTimeout;
        string encoding = root.TryGetProperty("encoding", out var enc) ? enc.GetString()!.ToLowerInvariant() : "file";
        string format = root.TryGetProperty("format", out var fmt) ? fmt.GetString()!.ToLowerInvariant() : "png";
        string outPath = root.TryGetProperty("out", out var o) ? o.GetString() : null;

        bool created;
        var cap = GetSession(cache, w, out created);
        FrameImage image;
        try
        {
            image = cap.Grab(timeout);
        }
        catch
        {
            // Session may be stale (window recreated / device lost): rebuild once.
            try { cap.Dispose(); } catch { }
            cache.Remove(w.Hwnd);
            cap = GetSession(cache, w, out created);
            image = cap.Grab(timeout);
        }

        if (crop)
        {
            int ox = w.ClientCropX, oy = w.ClientCropY;
            if (ox >= 0 && oy >= 0 && ox + w.ClientWidth <= image.Width && oy + w.ClientHeight <= image.Height)
                image = image.Crop(ox, oy, w.ClientWidth, w.ClientHeight);
        }

        double grabMs = sw.Elapsed.TotalMilliseconds;
        int imgW = image.Width, imgH = image.Height;
        string sha = null;
        long bytes = 0;
        string pngBase64 = null, rawBase64 = null;

        if (encoding == "base64")
        {
            using var ms = new MemoryStream();
            PngWriter.WriteBgra(ms, image, true, compression);
            var data = ms.ToArray();
            bytes = data.Length;
            sha = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(data)).ToLowerInvariant();
            pngBase64 = Convert.ToBase64String(data);
        }
        else if (encoding == "raw")
        {
            rawBase64 = Convert.ToBase64String(image.Bgra, 0, imgW * imgH * 4);
        }
        else
        {
            outPath ??= Path.Combine(Path.GetTempPath(), $"wincap-{DateTime.Now:HHmmssfff}.{format}");
            if (format == "bmp") BmpWriter.WriteBgra(outPath, image);
            else PngWriter.WriteBgra(outPath, image, true, compression);
            var fi = new FileInfo(outPath);
            bytes = fi.Length;
            sha = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(outPath))).ToLowerInvariant();
        }

        double totalMs = sw.Elapsed.TotalMilliseconds;

        return JsonSerializer.Serialize(new
        {
            ok = true,
            hwnd = $"0x{w.Hwnd:X}",
            pid = w.Pid,
            width = imgW,
            height = imgH,
            rawWidth = imgW,
            rawHeight = imgH,
            rawStride = imgW * 4,
            bgra = encoding == "raw",
            path = outPath,
            encoding,
            format,
            bytes,
            sha256 = sha,
            ms = Math.Round(totalMs, 1),
            grabMs = Math.Round(grabMs, 1),
            encodeMs = Math.Round(totalMs - grabMs, 1),
            newSession = created,
            pngBase64,
            rawBase64,
        }, Compact);
    }

    private static WgcWindowCapturer GetSession(Dictionary<long, WgcWindowCapturer> cache, WindowInfo w, out bool created)
    {
        if (cache.TryGetValue(w.Hwnd, out var cap) && cap != null)
        {
            created = false;
            return cap;
        }
        cap = WgcWindowCapturer.Create(w.Handle, hideBorder: true, includeCursor: false);
        cache[w.Hwnd] = cap;
        created = true;
        return cap;
    }
}
