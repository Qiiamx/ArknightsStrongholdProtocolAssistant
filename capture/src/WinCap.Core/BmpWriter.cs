namespace WinCap;

/// <summary>
/// 32-bit BGRA BMP writer. ~20x faster than PNG encoding because the pixel data is copied
/// verbatim; hardware-accelerated paint pipelines (PaddleOCR via OpenCV/PIL) read it fine.
/// </summary>
public static class BmpWriter
{
    public static void WriteBgra(string path, FrameImage frame)
    {
        string dir = Path.GetDirectoryName(Path.GetFullPath(path));
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
        using var fs = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 16);
        WriteBgra(fs, frame);
    }

    public static void WriteBgra(Stream s, FrameImage frame)
    {
        int w = frame.Width, h = frame.Height;
        int stride = w * 4;
        int imageSize = stride * h;
        const int headerSize = 14 + 40;

        Span<byte> header = stackalloc byte[headerSize];
        header[0] = (byte)'B'; header[1] = (byte)'M';
        WriteLe32(header, 2, (uint)(headerSize + imageSize));
        WriteLe16(header, 6, 0);
        WriteLe16(header, 8, 0);
        WriteLe32(header, 10, headerSize);

        WriteLe32(header, 14, 40);          // biSize
        WriteLe32(header, 18, (uint)w);     // biWidth
        WriteLe32(header, 22, (uint)(-h));  // biHeight (negative = top-down)
        WriteLe16(header, 26, 1);           // biPlanes
        WriteLe16(header, 28, 32);          // biBitCount
        WriteLe32(header, 30, 0);           // BI_RGB
        WriteLe32(header, 34, (uint)imageSize);
        WriteLe32(header, 38, 2835);        // 72 DPI
        WriteLe32(header, 42, 2835);
        WriteLe32(header, 46, 0);
        WriteLe32(header, 50, 0);

        s.Write(header);
        s.Write(frame.Bgra, 0, imageSize);
    }

    private static void WriteLe16(Span<byte> b, int o, int v)
    {
        b[o] = (byte)v;
        b[o + 1] = (byte)(v >> 8);
    }

    private static void WriteLe32(Span<byte> b, int o, uint v)
    {
        b[o] = (byte)v;
        b[o + 1] = (byte)(v >> 8);
        b[o + 2] = (byte)(v >> 16);
        b[o + 3] = (byte)(v >> 24);
    }
}
