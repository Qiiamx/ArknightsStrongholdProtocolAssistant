using System.IO.Compression;

namespace WinCap;

/// <summary>
/// Minimal, dependency-free PNG encoder for top-down BGRA frames.
/// PNG colour type 6 (RGBA, 8 bit), filter 0. No System.Drawing / WIC / GDI+ dependency.
/// </summary>
public static class PngWriter
{
    public enum Compression
    {
        /// <summary>zlib level 1 - fastest, decent size. Default.</summary>
        Fast,
        /// <summary>zlib level 9 - smallest, slowest.</summary>
        Best,
        /// <summary>No compression: largest file, fastest encode.</summary>
        Store,
    }

    private static readonly byte[] Signature = { 0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A };
    private static readonly uint[] CrcTable = BuildCrcTable();

    public static void WriteBgra(string path, FrameImage frame, bool forceOpaque = true, Compression level = Compression.Fast)
        => WriteBgra(path, frame.Width, frame.Height, frame.Bgra, forceOpaque, level);

    public static void WriteBgra(string path, int width, int height, byte[] bgra, bool forceOpaque = true, Compression level = Compression.Fast)
    {
        string dir = Path.GetDirectoryName(Path.GetFullPath(path));
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
        using var fs = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 16);
        WriteBgra(fs, width, height, bgra, forceOpaque, level);
    }

    public static void WriteBgra(Stream output, FrameImage frame, bool forceOpaque = true, Compression level = Compression.Fast)
        => WriteBgra(output, frame.Width, frame.Height, frame.Bgra, forceOpaque, level);

    public static void WriteBgra(Stream output, int width, int height, byte[] bgra, bool forceOpaque = true, Compression level = Compression.Fast)
    {
        int stride = width * 4;
        if (bgra.Length < (long)stride * height)
            throw new ArgumentException("Pixel buffer smaller than width*height*4.");

        // Raw PNG stream: each scanline is one filter byte (0 = None) followed by RGBA.
        int rawLen = height * (1 + stride);
        var raw = new byte[rawLen];
        for (int y = 0; y < height; y++)
        {
            int dst = y * (1 + stride);
            raw[dst] = 0;
            int src = y * stride;
            for (int x = 0; x < width; x++)
            {
                int s = src + x * 4, d = dst + 1 + x * 4;
                raw[d + 0] = bgra[s + 2]; // R
                raw[d + 1] = bgra[s + 1]; // G
                raw[d + 2] = bgra[s + 0]; // B
                raw[d + 3] = forceOpaque ? (byte)255 : bgra[s + 3];
            }
        }

        byte[] idat;
        if (level == Compression.Store)
        {
            idat = ZlibStore(raw);
        }
        else
        {
            using var ms = new MemoryStream(rawLen / 2);
            using (var z = new ZLibStream(ms, level == Compression.Best ? System.IO.Compression.CompressionLevel.SmallestSize : System.IO.Compression.CompressionLevel.Fastest, leaveOpen: true))
                z.Write(raw, 0, raw.Length);
            idat = ms.ToArray();
        }

        output.Write(Signature, 0, Signature.Length);
        var ihdr = new byte[13];
        WriteBe(ihdr, 0, (uint)width);
        WriteBe(ihdr, 4, (uint)height);
        ihdr[8] = 8;  // bit depth
        ihdr[9] = 6;  // colour type: truecolour + alpha
        ihdr[10] = 0; // compression: deflate
        ihdr[11] = 0; // filter method: adaptive
        ihdr[12] = 0; // interlace: none
        WriteChunk(output, "IHDR", ihdr);
        WriteChunk(output, "IDAT", idat);
        WriteChunk(output, "IEND", Array.Empty<byte>());
    }

    /// <summary>zlib stream with stored (uncompressed) deflate blocks - fastest possible valid PNG.</summary>
    private static byte[] ZlibStore(byte[] data)
    {
        const int blockMax = 65535;
        var ms = new MemoryStream(data.Length + data.Length / blockMax * 5 + 16);
        ms.WriteByte(0x78); // CMF: deflate, 32K window
        ms.WriteByte(0x01); // FLG: fastest, no dict, check bits ok
        int offset = 0;
        while (offset < data.Length)
        {
            int len = Math.Min(blockMax, data.Length - offset);
            bool last = offset + len >= data.Length;
            ms.WriteByte((byte)(last ? 1 : 0));
            ms.WriteByte((byte)(len & 0xFF));
            ms.WriteByte((byte)(len >> 8));
            ms.WriteByte((byte)(~len & 0xFF));
            ms.WriteByte((byte)((~len >> 8) & 0xFF));
            ms.Write(data, offset, len);
            offset += len;
        }
        uint a = 1, b = 0;
        foreach (byte v in data) { a = (a + v) % 65521; b = (b + a) % 65521; }
        uint adler = (b << 16) | a;
        ms.WriteByte((byte)(adler >> 24));
        ms.WriteByte((byte)(adler >> 16));
        ms.WriteByte((byte)(adler >> 8));
        ms.WriteByte((byte)adler);
        return ms.ToArray();
    }

    private static void WriteChunk(Stream s, string type, byte[] data)
    {
        var len = new byte[4];
        WriteBe(len, 0, (uint)data.Length);
        s.Write(len, 0, 4);
        var typeBytes = new byte[4];
        for (int i = 0; i < 4; i++) typeBytes[i] = (byte)type[i];
        s.Write(typeBytes, 0, 4);
        s.Write(data, 0, data.Length);
        uint crc = Crc32(typeBytes, 0, 4);
        crc = Crc32(data, 0, data.Length, crc);
        crc ^= 0xFFFFFFFF; // PNG CRC is the one's complement of the running CRC
        var crcBytes = new byte[4];
        WriteBe(crcBytes, 0, crc);
        s.Write(crcBytes, 0, 4);
    }

    private static uint Crc32(byte[] data, int offset, int count, uint crc = 0xFFFFFFFF)
    {
        for (int i = offset; i < offset + count; i++)
            crc = CrcTable[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);
        return crc;
    }

    private static uint[] BuildCrcTable()
    {
        var table = new uint[256];
        for (uint n = 0; n < 256; n++)
        {
            uint c = n;
            for (int k = 0; k < 8; k++)
                c = (c & 1) != 0 ? 0xEDB88320 ^ (c >> 1) : c >> 1;
            table[n] = c;
        }
        return table;
    }

    private static void WriteBe(byte[] buf, int offset, uint value)
    {
        buf[offset] = (byte)(value >> 24);
        buf[offset + 1] = (byte)(value >> 16);
        buf[offset + 2] = (byte)(value >> 8);
        buf[offset + 3] = (byte)value;
    }
}
