namespace WinCap;

/// <summary>A decoded frame: top-down 32-bit BGRA, stride = Width*4.</summary>
public sealed class FrameImage
{
    public int Width { get; }
    public int Height { get; }
    public int Stride { get; }
    public byte[] Bgra { get; }
    public long TimestampTicks { get; init; }
    public string Source { get; init; } = "unknown";

    public FrameImage(int width, int height, byte[] bgra, string source = "unknown")
    {
        if (bgra.Length < (long)width * height * 4)
            throw new ArgumentException($"Buffer too small: {bgra.Length} < {width * height * 4}");
        Width = width;
        Height = height;
        Stride = width * 4;
        Bgra = bgra;
        Source = source;
        TimestampTicks = DateTime.UtcNow.Ticks;
    }

    public ImageStats Stats() => ImageStats.FromBgra(Width, Height, Bgra);

    /// <summary>Returns a sub-rectangle (e.g. just the game client area, dropping the title bar).</summary>
    public FrameImage Crop(int x, int y, int width, int height)
    {
        x = Math.Clamp(x, 0, Width);
        y = Math.Clamp(y, 0, Height);
        width = Math.Clamp(width, 1, Width - x);
        height = Math.Clamp(height, 1, Height - y);
        var buf = new byte[(long)width * height * 4];
        for (int row = 0; row < height; row++)
            Buffer.BlockCopy(Bgra, (y + row) * Stride + x * 4, buf, row * width * 4, width * 4);
        return new FrameImage(width, height, buf, Source + "+crop");
    }

    public double MeanAbsDiff(FrameImage other, int x0 = 0, int y0 = 0, int x1 = -1, int y1 = -1)
    {
        if (other.Width != Width || other.Height != Height) return double.NaN;
        if (x1 < 0) x1 = Width;
        if (y1 < 0) y1 = Height;
        long sum = 0, n = 0;
        for (int y = y0; y < y1; y++)
        {
            int row = y * Stride;
            for (int x = x0; x < x1; x++)
            {
                int i = row + x * 4;
                sum += Math.Abs(Bgra[i] - other.Bgra[i]);
                sum += Math.Abs(Bgra[i + 1] - other.Bgra[i + 1]);
                sum += Math.Abs(Bgra[i + 2] - other.Bgra[i + 2]);
                n += 3;
            }
        }
        return n == 0 ? double.NaN : (double)sum / n;
    }

    /// <summary>Mean absolute per-channel difference over the top-left width x height region of both images.</summary>
    public double MeanAbsDiffTopLeft(FrameImage other, int width, int height)
    {
        width = Math.Min(width, Math.Min(Width, other.Width));
        height = Math.Min(height, Math.Min(Height, other.Height));
        long sum = 0, n = 0;
        for (int y = 0; y < height; y++)
        {
            int ra = y * Stride, rb = y * other.Stride;
            for (int x = 0; x < width; x++)
            {
                int ia = ra + x * 4, ib = rb + x * 4;
                sum += Math.Abs(Bgra[ia] - other.Bgra[ib]);
                sum += Math.Abs(Bgra[ia + 1] - other.Bgra[ib + 1]);
                sum += Math.Abs(Bgra[ia + 2] - other.Bgra[ib + 2]);
                n += 3;
            }
        }
        return n == 0 ? double.NaN : (double)sum / n;
    }

    public double DiffPixelRatio(FrameImage other, int threshold = 8)    {
        if (other.Width != Width || other.Height != Height) return double.NaN;
        long changed = 0, total = 0;
        for (int y = 0; y < Height; y += 3)
        {
            int row = y * Stride;
            for (int x = 0; x < Width; x += 3)
            {
                int i = row + x * 4;
                int d = Math.Abs(Bgra[i] - other.Bgra[i])
                      + Math.Abs(Bgra[i + 1] - other.Bgra[i + 1])
                      + Math.Abs(Bgra[i + 2] - other.Bgra[i + 2]);
                if (d > threshold) changed++;
                total++;
            }
        }
        return total == 0 ? double.NaN : (double)changed / total;
    }
}

/// <summary>Cheap image statistics used to prove a capture is not blank/white/black.</summary>
public sealed class ImageStats
{
    public double MeanR { get; init; }
    public double MeanG { get; init; }
    public double MeanB { get; init; }
    public double MeanA { get; init; }
    public double WhiteRatio { get; init; }
    public double BlackRatio { get; init; }
    public double UniqueColorRatio { get; init; }
    public int SampledPixels { get; init; }

    public bool LooksBlank => WhiteRatio > 0.98 || BlackRatio > 0.98 || UniqueColorRatio < 0.0005;

    public static ImageStats FromBgra(int w, int h, byte[] bgra)
    {
        long sr = 0, sg = 0, sb = 0, sa = 0, white = 0, black = 0, n = 0;
        var set = new HashSet<int>();
        int step = Math.Max(1, Math.Min(w, h) / 240); // sample a grid, at most ~240x240 points
        for (int y = 0; y < h; y += step)
        {
            int row = y * w * 4;
            for (int x = 0; x < w; x += step)
            {
                int i = row + x * 4;
                byte b = bgra[i], g = bgra[i + 1], r = bgra[i + 2], a = bgra[i + 3];
                sr += r; sg += g; sb += b; sa += a;
                if (r > 250 && g > 250 && b > 250) white++;
                if (r < 6 && g < 6 && b < 6) black++;
                if (set.Count < 4096) set.Add((r << 16) | (g << 8) | b);
                n++;
            }
        }
        return new ImageStats
        {
            MeanR = n == 0 ? 0 : (double)sr / n,
            MeanG = n == 0 ? 0 : (double)sg / n,
            MeanB = n == 0 ? 0 : (double)sb / n,
            MeanA = n == 0 ? 0 : (double)sa / n,
            WhiteRatio = n == 0 ? 1 : (double)white / n,
            BlackRatio = n == 0 ? 1 : (double)black / n,
            UniqueColorRatio = n == 0 ? 0 : (double)set.Count / n,
            SampledPixels = (int)n,
        };
    }

    public override string ToString() =>
        $"mean=({MeanR:F1},{MeanG:F1},{MeanB:F1}) a={MeanA:F0} white={WhiteRatio:P1} black={BlackRatio:P1} colors={UniqueColorRatio:P2}";
}
