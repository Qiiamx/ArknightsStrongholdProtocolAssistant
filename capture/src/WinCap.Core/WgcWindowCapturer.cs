using Windows.Foundation;
using Windows.Graphics;
using Windows.Graphics.Capture;
using Windows.Graphics.DirectX;
using Windows.Graphics.DirectX.Direct3D11;
using Windows.Graphics.Imaging;
using Windows.Storage.Streams;

namespace WinCap;

/// <summary>
/// Windows.Graphics.Capture (WGC) capture of a single top-level window.
/// WGC reads the composited window surface from DWM, so it does not care whether the window is
/// foreground or covered by other windows (it does require the window not to be minimised).
/// </summary>
public sealed class WgcWindowCapturer : IDisposable
{
    private IDirect3DDevice _device;
    private Direct3D11CaptureFramePool _pool;
    private GraphicsCaptureSession _session;
    private int _poolWidth, _poolHeight;
    private AutoResetEvent _frameSignal;
    private bool _disposed;

    public GraphicsCaptureItem Item { get; private set; }
    public string DeviceKind { get; private set; } = "unknown";
    public string ItemApi { get; private set; } = "unknown";
    public bool BorderHidden { get; private set; }
    public bool CursorIncluded { get; private set; }
    public string ItemError { get; private set; }
    public SizeInt32 PoolSize => new() { Width = _poolWidth, Height = _poolHeight };
    public SizeInt32 ItemSize { get; private set; }

    private WgcWindowCapturer() { }

    /// <summary>
    /// Opens a capture session for <paramref name="hwnd"/>. Keep the instance alive to capture
    /// repeatedly (warm path); dispose it when done.
    /// </summary>
    /// <param name="hideBorder">
    /// Suppress the yellow "you are being captured" border that Windows 11 draws around the
    /// captured window. Without this, every capture flashes a border around the game window.
    /// </param>
    public static WgcWindowCapturer Create(IntPtr hwnd, bool hideBorder = true, bool includeCursor = false, int bufferCount = 3)
    {
        if (hwnd == IntPtr.Zero) throw new ArgumentException("hwnd is null");

        var c = new WgcWindowCapturer();
        try
        {
            c._device = CaptureInterop.CreateDirect3DDevice(out string kind);
            c.DeviceKind = kind;

            try
            {
                c.Item = CaptureInterop.CreateItemForWindow(hwnd);
                c.ItemApi = "IGraphicsCaptureItemInterop::CreateForWindow";
            }
            catch (Exception ex)
            {
                c.ItemError = ex.GetType().Name + ": " + ex.Message;
                c.Item = CaptureInterop.TryCreateItemFromWindowId(hwnd);
                if (c.Item == null) throw new InvalidOperationException("CreateForWindow failed: " + ex.Message, ex);
                c.ItemApi = "GraphicsCaptureItem.TryCreateFromWindowId";
            }

            c.ItemSize = c.Item.Size;
            c._poolWidth = c.ItemSize.Width;
            c._poolHeight = c.ItemSize.Height;
            c._frameSignal = new AutoResetEvent(false);

            c._pool = Direct3D11CaptureFramePool.CreateFreeThreaded(
                c._device, DirectXPixelFormat.B8G8R8A8UIntNormalized, Math.Max(2, bufferCount), c.ItemSize);

            c._session = c._pool.CreateCaptureSession(c.Item);

            try
            {
                c._session.IsBorderRequired = !hideBorder;
                c.BorderHidden = hideBorder;
            }
            catch
            {
                c.BorderHidden = false; // pre-Win11 or restricted
            }

            c.CursorIncluded = includeCursor;
            try { c._session.IsCursorCaptureEnabled = includeCursor; } catch { }

            c._pool.FrameArrived += c.OnFrameArrived;
            c._session.StartCapture();
            return c;
        }
        catch
        {
            c.Dispose();
            throw;
        }
    }

    private void OnFrameArrived(Direct3D11CaptureFramePool sender, object args) => _frameSignal.Set();

    /// <summary>Grabs the newest composed frame and copies it to CPU memory as BGRA.</summary>
    public FrameImage Grab(int timeoutMs = 3000)
    {
        ThrowIfDisposed();
        var frame = TakeLatestFrame(timeoutMs);
        try
        {
            var content = frame.ContentSize;
            if (content.Width != _poolWidth || content.Height != _poolHeight)
            {
                // Window was resized: the pool must be recreated.
                _pool.Recreate(_device, DirectXPixelFormat.B8G8R8A8UIntNormalized, 3, content);
                _poolWidth = content.Width;
                _poolHeight = content.Height;
            }

            using var bitmap = ToSoftwareBitmap(frame);
            return ToFrameImage(bitmap, "wgc");
        }
        finally
        {
            frame.Dispose();
        }
    }

    /// <summary>Non-blocking: true when at least one frame is already queued in the pool.</summary>
    public bool HasQueuedFrame
    {
        get
        {
            ThrowIfDisposed();
            var f = _pool.TryGetNextFrame();
            if (f == null) return false;
            f.Dispose();
            return true;
        }
    }

    private Direct3D11CaptureFrame TakeLatestFrame(int timeoutMs)
    {
        var latest = Drain();
        if (latest != null) return latest;

        _frameSignal.Reset();
        if (!_frameSignal.WaitOne(timeoutMs))
            throw new TimeoutException($"WGC produced no frame within {timeoutMs} ms (is the window minimised or already closed?)");

        latest = Drain();
        return latest ?? throw new TimeoutException("FrameArrived fired but no frame was available.");
    }

    private Direct3D11CaptureFrame Drain()
    {
        Direct3D11CaptureFrame latest = null;
        for (int i = 0; i < 8; i++)
        {
            var f = _pool.TryGetNextFrame();
            if (f == null) break;
            latest?.Dispose();
            latest = f;
        }
        return latest;
    }

    private static SoftwareBitmap ToSoftwareBitmap(Direct3D11CaptureFrame frame)
    {
        // CreateCopyFromSurfaceAsync does the GPU->CPU staging copy for us; no manual D3D11 needed.
        var op = SoftwareBitmap.CreateCopyFromSurfaceAsync(frame.Surface, BitmapAlphaMode.Premultiplied);
        var bitmap = op.AsTask().GetAwaiter().GetResult();

        if (bitmap.BitmapPixelFormat == BitmapPixelFormat.Bgra8) return bitmap;
        var converted = SoftwareBitmap.Convert(bitmap, BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied);
        bitmap.Dispose();
        return converted;
    }

    private static FrameImage ToFrameImage(SoftwareBitmap bitmap, string source)
    {
        int w = bitmap.PixelWidth, h = bitmap.PixelHeight;
        uint length = (uint)(w * h * 4);
        var buffer = new Windows.Storage.Streams.Buffer(length) { Length = length };
        bitmap.CopyToBuffer(buffer);

        var bytes = new byte[length];
        using (var reader = DataReader.FromBuffer(buffer))
            reader.ReadBytes(bytes);

        return new FrameImage(w, h, bytes, source);
    }

    private void ThrowIfDisposed()
    {
        if (_disposed) throw new ObjectDisposedException(nameof(WgcWindowCapturer));
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        try
        {
            if (_pool != null) _pool.FrameArrived -= OnFrameArrived;
            _session?.Dispose();
            _pool?.Dispose();
        }
        catch { /* best effort */ }
        _session = null;
        _pool = null;
        _device = null;
        _frameSignal?.Dispose();
        _frameSignal = null;
    }
}
