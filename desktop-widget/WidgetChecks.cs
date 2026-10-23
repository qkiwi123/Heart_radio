// Component checks only: no window is shown and no mouse input is synthesized.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows.Controls.Primitives;
using System.Windows.Interop;
using System.Web.Script.Serialization;
using System.Threading.Tasks;
using HeartRadioDesktop;

class WidgetChecks {
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr window,uint message,IntPtr wParam,IntPtr lParam);
    static int count;
    static void Check(bool condition,string description) {
        if(!condition)throw new Exception(description);
        count++;Console.WriteLine("PASS: "+description);
    }
    [STAThread] static int Main(string[] args) {
        try {
            if(args.Length!=1)throw new ArgumentException("Expected a temporary layout path");
            if(File.Exists(args[0]))File.Delete(args[0]);
            var submissions=new List<Dictionary<string,object>>();bool failSave=false;
            Func<Dictionary<string,object>,Task<Dictionary<string,object>>> sender=value=>{
                submissions.Add(value);
                if(failSave){var failed=new TaskCompletionSource<Dictionary<string,object>>();failed.SetException(new IOException("simulated offline"));return failed.Task;}
                return Task.FromResult(new Dictionary<string,object>{{"status","saved"}});
            };
            var host=new Host(8766,Process.GetCurrentProcess().Id,args[0],sender);
            host.CurrentWidget=new WidgetWindow(host,"current");host.TrendWidget=new WidgetWindow(host,"trend");
            var current=host.CurrentWidget;var trend=host.TrendWidget;
            var grip=(Thumb)typeof(WidgetWindow).GetField("resizeGrip",BindingFlags.Instance|BindingFlags.NonPublic).GetValue(trend);
            trend.SetLocked(true);
            Check(trend.Locked&&!trend.Pin.IsEnabled&&!trend.CloseButton.IsEnabled&&!grip.IsEnabled,"full lock disables pin, close and resize");
            Check(trend.Pin.Visibility==System.Windows.Visibility.Collapsed&&trend.CloseButton.Visibility==System.Windows.Visibility.Collapsed&&grip.Visibility==System.Windows.Visibility.Collapsed,"locked window hides controls and resize grip");
            Check(trend.OwnedWindows.Count==0,"locked window does not create a separate clickable overlay");
            Check(!current.Locked&&current.Pin.IsEnabled&&current.CloseButton.IsEnabled,"other window stays independent");
            trend.SetLocked(false);
            Check(!trend.Locked&&trend.Pin.IsEnabled&&trend.CloseButton.IsEnabled&&grip.IsEnabled&&trend.Pin.Visibility==System.Windows.Visibility.Visible&&trend.CloseButton.Visibility==System.Windows.Visibility.Visible&&grip.Visibility==System.Windows.Visibility.Visible,"unlock restores window controls");
            current.Width=360;current.Height=240;trend.Width=540;trend.Height=310;trend.SetLocked(false);
            host.SaveLayout();
            Check(!File.Exists(args[0])&&submissions.Count==2,"widget sends layout patches and never writes the shared layout file");
            Check(!((Dictionary<string,object>)submissions[0]["layout"]).ContainsKey("locked"),"geometry patch cannot overwrite dashboard lock state");
            File.WriteAllText(args[0],"{\"current\":{\"width\":360,\"height\":240,\"locked\":false},\"trend\":{\"width\":540,\"height\":310,\"locked\":true}}");
            current.Width=300;current.Height=198;trend.Width=480;trend.Height=264;trend.SetLocked(false);
            var restore=typeof(Host).GetMethod("RestoreLayout",BindingFlags.Instance|BindingFlags.NonPublic);
            restore.Invoke(host,null);
            Check(current.Width==360&&current.Height==240&&trend.Width==540&&trend.Height==310,"each window size is restored independently");
            Check(trend.Locked&&!current.Locked&&!grip.IsEnabled,"full lock is restored with size");
            File.WriteAllText(args[0],"{\"current\":{\"locked\":true},\"trend\":{\"locked\":false}}");
            var unchanged=File.ReadAllText(args[0]);
            host.SaveLayout();
            var saved=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(File.ReadAllText(args[0]));
            var currentSaved=(IDictionary<string,object>)saved["current"];
            var trendSaved=(IDictionary<string,object>)saved["trend"];
            Check(Object.Equals(currentSaved["locked"],true)&&Object.Equals(trendSaved["locked"],false),"desktop layout save preserves dashboard lock changes pending the next poll");
            Check(File.ReadAllText(args[0])==unchanged,"pending save leaves authoritative file byte-for-byte unchanged");
            current.SetLocked(false);failSave=true;host.SaveLayout("current");
            Check(host.LayoutError!=null&&host.PendingLayouts==1,"failed asynchronous save stays pending and reports unsaved layout");
            failSave=false;host.SaveLayout("current");
            Check(host.LayoutError==null&&host.PendingLayouts==0,"a new completed gesture can recover a failed save");
            File.WriteAllText(args[0],"{\"current\":{\"movementLocked\":true},\"trend\":{\"locked\":false}}");
            restore.Invoke(host,null);
            Check(current.Locked&&!trend.Locked,"legacy saved movement lock upgrades to full lock");
            new WindowInteropHelper(current).EnsureHandle();
            new WindowInteropHelper(trend).EnsureHandle();
            Check(current.Icon!=null&&trend.Icon!=null&&Object.ReferenceEquals(current.Icon,trend.Icon),"both widget windows load the shared embedded application icon");
            Check(SendMessage(new WindowInteropHelper(current).Handle,0x7F,new IntPtr(1),IntPtr.Zero)!=IntPtr.Zero&&SendMessage(new WindowInteropHelper(trend).Handle,0x7F,new IntPtr(1),IntPtr.Zero)!=IntPtr.Zero,"both native widget windows expose a large taskbar icon");
            Check(SendMessage(new WindowInteropHelper(current).Handle,0x7F,IntPtr.Zero,IntPtr.Zero)!=IntPtr.Zero&&SendMessage(new WindowInteropHelper(trend).Handle,0x7F,IntPtr.Zero,IntPtr.Zero)!=IntPtr.Zero,"both native widget windows expose a small taskbar icon");
            Check(current.NativeClickThrough,"restored lock enables native WS_EX_TRANSPARENT");
            current.SetLocked(false);
            Check(!current.NativeClickThrough,"unlock removes native click-through style");
            current.SetLocked(true);
            Check(current.NativeClickThrough,"locking again restores native click-through style");
            host.Exiting=true;current.Close();trend.Close();
            Console.WriteLine(count+" component checks passed; native drag interaction is a separate manual check.");
            return 0;
        } catch(Exception error) {Console.Error.WriteLine(error);return 1;}
    }
}
