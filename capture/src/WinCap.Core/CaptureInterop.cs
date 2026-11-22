using System.Runtime.InteropServices;
using Windows.Graphics.Capture;
using Windows.Graphics.DirectX.Direct3D11;

namespace WinCap;

/// <summary>
/// Creates the WinRT <see cref="IDirect3DDevice"/> that Windows.Graphics.Capture needs, and
/// creates a <see cref="GraphicsCaptureItem"/> for a Win32 HWND.
/// Both need ABI-level COM interop that the C#/WinRT projections do not expose directly.
/// </summary>
public static class CaptureInterop
{
    private const int D3D_DRIVER_TYPE_HARDWARE = 1;
    private const int D3D_DRIVER_TYPE_WARP = 5;
    private const uint D3D11_CREATE_DEVICE_BGRA_SUPPORT = 0x20;
    private const uint D3D11_SDK_VERSION = 7;

    private static readonly Guid IID_IDXGIDevice = new("54ec77fa-1377-44e6-8c32-88fd5f44c84c");
    private static readonly Guid IID_IGraphicsCaptureItemInterop = new("3628e81b-3cac-4c60-b7f4-23ce0e0c3356");
    /// <summary>IID of the IGraphicsCaptureItem default interface (a runtime class GUID will NOT do).</summary>
    private static readonly Guid IID_IGraphicsCaptureItem = new("79c3f95b-31f7-4ec2-a464-632ef5d30760");

    [DllImport("d3d11.dll", ExactSpelling = true)]
    private static extern int D3D11CreateDevice(
        IntPtr pAdapter, int driverType, IntPtr software, uint flags,
        IntPtr pFeatureLevels, uint featureLevels, uint sdkVersion,
        out IntPtr ppDevice, out int pFeatureLevel, out IntPtr ppImmediateContext);

    [DllImport("d3d11.dll", ExactSpelling = true)]
    private static extern int CreateDirect3D11DeviceFromDXGIDevice(IntPtr dxgiDevice, out IntPtr graphicsDevice);

    // RoGetActivationFactory is the canonical way to get the real COM activation factory. Going
    // through WinRT.ActivationFactory.Get() and QI'ing its pointer for IGraphicsCaptureItemInterop
    // fails with E_NOINTERFACE (the projection hands out a managed factory wrapper), so we ask
    // combase directly for the interop interface.
    [DllImport("combase.dll", ExactSpelling = true)]
    private static extern int RoGetActivationFactory(IntPtr activatableClassId, ref Guid iid, out IntPtr factory);

    [DllImport("combase.dll", ExactSpelling = true, CharSet = CharSet.Unicode)]
    private static extern int WindowsCreateString(string sourceString, uint length, out IntPtr hstring);

    [DllImport("combase.dll", ExactSpelling = true)]
    private static extern int WindowsDeleteString(IntPtr hstring);

    private const string GraphicsCaptureItemClass = "Windows.Graphics.Capture.GraphicsCaptureItem";

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate int CreateForWindowFn(IntPtr thisPtr, IntPtr hwnd, ref Guid iid, out IntPtr item);

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    private delegate int CreateForMonitorFn(IntPtr thisPtr, IntPtr hmonitor, ref Guid iid, out IntPtr item);

    /// <summary>Creates a hardware (falling back to WARP) D3D11 device wrapped as a WinRT IDirect3DDevice.</summary>
    public static IDirect3DDevice CreateDirect3DDevice(out string kind)
    {
        int hr = D3D11CreateDevice(IntPtr.Zero, D3D_DRIVER_TYPE_HARDWARE, IntPtr.Zero,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, IntPtr.Zero, 0, D3D11_SDK_VERSION,
            out IntPtr device, out _, out IntPtr context);

        kind = "hardware";
        if (hr < 0)
        {
            hr = D3D11CreateDevice(IntPtr.Zero, D3D_DRIVER_TYPE_WARP, IntPtr.Zero,
                D3D11_CREATE_DEVICE_BGRA_SUPPORT, IntPtr.Zero, 0, D3D11_SDK_VERSION,
                out device, out _, out context);
            kind = "warp";
        }
        Marshal.ThrowExceptionForHR(hr);

        try
        {
            Guid iidDxgi = IID_IDXGIDevice;
            Marshal.ThrowExceptionForHR(Marshal.QueryInterface(device, ref iidDxgi, out IntPtr dxgiDevice));
            try
            {
                Marshal.ThrowExceptionForHR(CreateDirect3D11DeviceFromDXGIDevice(dxgiDevice, out IntPtr inspectable));
                // FromAbi takes ownership of the reference produced by CreateDirect3D11DeviceFromDXGIDevice.
                return WinRT.MarshalInterface<IDirect3DDevice>.FromAbi(inspectable);
            }
            finally
            {
                Marshal.Release(dxgiDevice);
            }
        }
        finally
        {
            Marshal.Release(device);
            if (context != IntPtr.Zero) Marshal.Release(context);
        }
    }

    /// <summary>IGraphicsCaptureItemInterop::CreateForWindow - the classic way to get a window capture item.</summary>
    public static GraphicsCaptureItem CreateItemForWindow(IntPtr hwnd)
    {
        IntPtr interop = GetItemInterop();
        try
        {
            IntPtr vtbl = Marshal.ReadIntPtr(interop);
            IntPtr fnPtr = Marshal.ReadIntPtr(vtbl, 3 * IntPtr.Size); // IUnknown(3) + CreateForWindow
            var fn = Marshal.GetDelegateForFunctionPointer<CreateForWindowFn>(fnPtr);
            Guid iidItem = IID_IGraphicsCaptureItem;
            int hr = fn(interop, hwnd, ref iidItem, out IntPtr item);
            Marshal.ThrowExceptionForHR(hr);
            return GraphicsCaptureItem.FromAbi(item);
        }
        finally
        {
            Marshal.Release(interop);
        }
    }

    private static IntPtr GetItemInterop()
    {
        int hr = WindowsCreateString(GraphicsCaptureItemClass, (uint)GraphicsCaptureItemClass.Length, out IntPtr hstr);
        Marshal.ThrowExceptionForHR(hr);
        try
        {
            Guid iid = IID_IGraphicsCaptureItemInterop;
            hr = RoGetActivationFactory(hstr, ref iid, out IntPtr interop);
            Marshal.ThrowExceptionForHR(hr);
            return interop;
        }
        finally
        {
            WindowsDeleteString(hstr);
        }
    }

    /// <summary>Newer (Win11) path: GraphicsCaptureItem.TryCreateFromWindowId - no COM interop needed.</summary>
    public static GraphicsCaptureItem TryCreateItemFromWindowId(IntPtr hwnd)
    {
        try
        {
            var id = new Windows.UI.WindowId { Value = (ulong)(long)hwnd };
            return GraphicsCaptureItem.TryCreateFromWindowId(id);
        }
        catch
        {
            return null;
        }
    }

    /// <summary>IGraphicsCaptureItemInterop::CreateForMonitor (whole-display capture), used for comparison.</summary>
    public static GraphicsCaptureItem CreateItemForMonitor(IntPtr hmonitor)
    {
        IntPtr interop = GetItemInterop();
        try
        {
            IntPtr vtbl = Marshal.ReadIntPtr(interop);
            IntPtr fnPtr = Marshal.ReadIntPtr(vtbl, 4 * IntPtr.Size); // IUnknown(3) + CreateForWindow + CreateForMonitor
            var fn = Marshal.GetDelegateForFunctionPointer<CreateForMonitorFn>(fnPtr);
            Guid iidItem = IID_IGraphicsCaptureItem;
            int hr = fn(interop, hmonitor, ref iidItem, out IntPtr item);
            Marshal.ThrowExceptionForHR(hr);
            return GraphicsCaptureItem.FromAbi(item);
        }
        finally
        {
            Marshal.Release(interop);
        }
    }
}
