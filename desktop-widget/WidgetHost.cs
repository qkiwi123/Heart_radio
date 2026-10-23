using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Effects;
using System.Windows.Media.Imaging;
using System.Reflection;
using System.Windows.Threading;
using System.Windows.Interop;
using System.Runtime.InteropServices;

namespace HeartRadioDesktop {
    public static class AppIcon {
        public static readonly BitmapSource Source=Load();
        static BitmapSource Load(){
            using(var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("HeartRadio.AppIcon")){
                if(stream==null)throw new InvalidOperationException("HeartRadio application icon is missing from this build.");
                var icon=BitmapFrame.Create(stream,BitmapCreateOptions.PreservePixelFormat,BitmapCacheOption.OnLoad);
                icon.Freeze();return icon;
            }
        }
    }
    public static class Data {
        public static object Get(IDictionary<string,object> d,string key) { object v; return d.TryGetValue(key,out v)?v:null; }
        public static double Number(IDictionary<string,object> d,string key,double fallback=0) { object v=Get(d,key); return v==null?fallback:Convert.ToDouble(v,CultureInfo.InvariantCulture); }
        public static bool Flag(IDictionary<string,object> d,string key) { return Object.Equals(Get(d,key),true); }
        public static string Text(IDictionary<string,object> d,string key) { return Convert.ToString(Get(d,key),CultureInfo.InvariantCulture); }
        public static readonly Brush HeartRed=new SolidColorBrush(Color.FromRgb(255,107,122));
        public static readonly Brush Muted=new SolidColorBrush(Color.FromRgb(202,216,218));
        public static TextBlock Label(string text,double size,Brush color) { return new TextBlock {Text=text,FontSize=size,Foreground=color,FontFamily=new FontFamily("Microsoft YaHei"),VerticalAlignment=VerticalAlignment.Center}; }
    }

    public class Sample { public double Time,Bpm,Segment; }
    public class Trend : FrameworkElement {
        List<Sample> samples=new List<Sample>(); double end;
        public void Update(IDictionary<string,object> state) {
            end=Data.Number(state,"endedMs",Data.Number(state,"nowMs")); samples.Clear();
            IEnumerable rows=Data.Get(state,"points") as IEnumerable;
            if(rows!=null) foreach(object item in rows) { var p=item as IDictionary<string,object>; if(p!=null) samples.Add(new Sample{Time=Data.Number(p,"timeMs"),Bpm=Data.Number(p,"bpm"),Segment=Data.Number(p,"segment")}); }
            InvalidateVisual();
        }
        void Text(DrawingContext dc,string text,double x,double y) { dc.DrawText(new FormattedText(text,CultureInfo.CurrentCulture,FlowDirection.LeftToRight,new Typeface("Segoe UI"),11,Data.Muted,VisualTreeHelper.GetDpi(this).PixelsPerDip),new Point(x,y)); }
        protected override void OnRender(DrawingContext dc) {
            base.OnRender(dc); double w=Math.Max(100,ActualWidth),h=Math.Max(60,ActualHeight),left=34,right=w-14,top=8,bottom=h-26,min=50,max=110;
            if(samples.Count>0) { min=Double.MaxValue;max=Double.MinValue; foreach(var p in samples){min=Math.Min(min,p.Bpm);max=Math.Max(max,p.Bpm);} min=Math.Floor((min-8)/10)*10;max=Math.Ceiling((max+8)/10)*10;if(max-min<30){min-=10;max+=10;} }
            Pen grid=new Pen(new SolidColorBrush(Color.FromArgb(85,170,197,191)),1);grid.DashStyle=DashStyles.Dot;
            for(int i=0;i<4;i++){double y=top+(bottom-top)*i/3;dc.DrawLine(grid,new Point(left,y),new Point(right,y));Text(dc,Math.Round(max-(max-min)*i/3).ToString(),2,y-7);}
            double start=end-600000; Func<double,double> x=t=>left+(t-start)/600000*(right-left); Func<double,double> yy=v=>bottom-(v-min)/(max-min)*(bottom-top);
            Pen line=new Pen(Data.HeartRed,2);Sample previous=null;
            foreach(var p in samples){if(p.Time<start)continue;if(previous!=null&&previous.Segment==p.Segment&&p.Time-previous.Time<=30000)dc.DrawLine(line,new Point(x(previous.Time),yy(previous.Bpm)),new Point(x(p.Time),yy(p.Bpm)));else dc.DrawEllipse(Data.HeartRed,null,new Point(x(p.Time),yy(p.Bpm)),2,2);previous=p;}
            if(end>0) for(int i=0;i<3;i++){double t=start+300000*i;string label=new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc).AddMilliseconds(t).ToLocalTime().ToString("HH:mm");Text(dc,label,left+(right-left)*i/2-(i==2?30:0),h-17);}
            if(samples.Count==0)Text(dc,"暂无有效心率事件",Math.Max(left,w/2-55),h/2-10);
        }
    }

    public class WidgetWindow : Window {
        [DllImport("user32.dll",EntryPoint="GetWindowLongW")] static extern int GetWindowLong(IntPtr window,int index);
        [DllImport("user32.dll",EntryPoint="SetWindowLongW")] static extern int SetWindowLong(IntPtr window,int index,int value);
        const int ExtendedStyle=-20,TransparentStyle=0x20,NoActivateStyle=0x08000000;
        public bool NativeClickThrough { get {var handle=new WindowInteropHelper(this).Handle;return handle!=IntPtr.Zero&&(GetWindowLong(handle,ExtendedStyle)&TransparentStyle)!=0;} }
        public readonly string Kind;
        public TextBlock Value,Status,Age,HeartUnit;
        public Trend Chart; public Button Pin,CloseButton; public bool Locked; Border dragArea; Thumb resizeGrip;
        readonly Host host;
        public WidgetWindow(Host owner,string kind) {
            host=owner;Kind=kind;Title=kind=="current"?"HeartRadio · 桌面心率":"HeartRadio · 桌面趋势";
            Icon=AppIcon.Source;
            Width=kind=="current"?300:480;Height=kind=="current"?198:264;
            MinWidth=kind=="current"?300:380;MinHeight=kind=="current"?198:220;
            MaxWidth=SystemParameters.VirtualScreenWidth;MaxHeight=SystemParameters.VirtualScreenHeight;
            WindowStyle=WindowStyle.None;AllowsTransparency=true;Background=Brushes.Transparent;ResizeMode=ResizeMode.NoResize;
            ShowInTaskbar=true;ShowActivated=false;Topmost=true;
            Left=SystemParameters.WorkArea.Right-Width-24;Top=SystemParameters.WorkArea.Top+(kind=="current"?64:278);
            var panel=new Grid{Margin=new Thickness(12),Background=Brushes.Transparent};
            panel.RowDefinitions.Add(new RowDefinition{Height=new GridLength(30)});panel.RowDefinitions.Add(new RowDefinition{Height=new GridLength(1,GridUnitType.Star)});panel.RowDefinitions.Add(new RowDefinition{Height=new GridLength(25)});
            var header=new DockPanel{Background=Brushes.Transparent,Cursor=Cursors.SizeAll};
            var close=Button("×","关闭这个桌面小窗");CloseButton=close;DockPanel.SetDock(close,Dock.Right);header.Children.Add(close);
            var pin=Button("置顶 ✓","切换置顶");Pin=pin;DockPanel.SetDock(pin,Dock.Right);header.Children.Add(pin);
            var dragTitle=new Border{Background=Brushes.Transparent,Child=Data.Label(kind=="current"?"♥  当前心率":"心率变化 · 近 10 分钟",12,kind=="current"?Data.HeartRed:Data.Muted),ToolTip="拖动此处移动小窗"};header.Children.Add(dragTitle);dragArea=dragTitle;
            dragTitle.MouseLeftButtonDown+=(s,e)=>{if(Locked)return;if(e.OriginalSource is TextBlock && ((TextBlock)e.OriginalSource).Text=="×")return;try{DragMove();host.SaveLayout(Kind);}catch(InvalidOperationException){} };
            close.Click+=async(s,e)=>{Hide();await host.Hide(Kind);};
            pin.Click+=(s,e)=>{Topmost=!Topmost;pin.Content=Topmost?"置顶 ✓":"置顶";host.SaveLayout(Kind);};
            panel.Children.Add(header);
            var center=new StackPanel{VerticalAlignment=VerticalAlignment.Center};Grid.SetRow(center,1);
            if(kind=="current"){
                var numberRow=new StackPanel{Orientation=Orientation.Horizontal};Value=Data.Label("—",68,Data.HeartRed);Value.FontWeight=FontWeights.SemiBold;
                numberRow.Children.Add(Value);var unit=Data.Label("bpm",16,Data.Muted);HeartUnit=unit;unit.Margin=new Thickness(12,30,0,0);numberRow.Children.Add(unit);center.Children.Add(numberRow);
                Status=Data.Label("等待本地数据",12,Data.Muted);center.Children.Add(Status);panel.Children.Add(center);
            }else{Chart=new Trend();Grid.SetRow(Chart,1);panel.Children.Add(Chart);}
            Age=Data.Label(kind=="current"?"—":"有效事件 · 空档不补值",11,Data.Muted);Age.Margin=new Thickness(0,0,26,0);Age.TextTrimming=TextTrimming.CharacterEllipsis;Grid.SetRow(Age,2);panel.Children.Add(Age);
            var resize=new Thumb{Width=22,Height=22,HorizontalAlignment=HorizontalAlignment.Right,VerticalAlignment=VerticalAlignment.Bottom,Cursor=Cursors.SizeNWSE,ToolTip="拖动右下角调整大小"};
            resizeGrip=resize;
            var resizeBorder=new FrameworkElementFactory(typeof(Border));resizeBorder.SetValue(Border.BackgroundProperty,Brushes.Transparent);
            var resizeSymbol=new FrameworkElementFactory(typeof(TextBlock));resizeSymbol.SetValue(TextBlock.TextProperty,"◢");resizeSymbol.SetValue(TextBlock.FontSizeProperty,17.0);resizeSymbol.SetValue(TextBlock.ForegroundProperty,Data.Muted);resizeSymbol.SetValue(TextBlock.HorizontalAlignmentProperty,HorizontalAlignment.Right);resizeBorder.AppendChild(resizeSymbol);
            resize.Template=new ControlTemplate(typeof(Thumb)){VisualTree=resizeBorder};Grid.SetRow(resize,2);panel.Children.Add(resize);
            Point resizeOrigin=new Point();double resizeWidth=0,resizeHeight=0;
            resize.DragStarted+=(s,e)=>{resizeOrigin=PointToScreen(Mouse.GetPosition(this));resizeWidth=Width;resizeHeight=Height;};
            resize.DragDelta+=(s,e)=>{if(Locked)return;var point=PointToScreen(Mouse.GetPosition(this));var dpi=VisualTreeHelper.GetDpi(this);Width=Math.Max(MinWidth,Math.Min(MaxWidth,resizeWidth+(point.X-resizeOrigin.X)/dpi.DpiScaleX));Height=Math.Max(MinHeight,Math.Min(MaxHeight,resizeHeight+(point.Y-resizeOrigin.Y)/dpi.DpiScaleY));};
            resize.DragCompleted+=(s,e)=>host.SaveLayout(Kind);
            SizeChanged+=(s,e)=>{if(Value!=null){double scale=Math.Min(ActualWidth/300,ActualHeight/198);Value.FontSize=Math.Min(180,68*scale);HeartUnit.FontSize=Math.Min(40,16*scale);HeartUnit.Margin=new Thickness(12,Math.Min(75,30*scale),0,0);}};
            Content=panel;
            SourceInitialized+=(s,e)=>ApplyNativeLock();
            Closing+=async(s,e)=>{if(host.Exiting)return;e.Cancel=true;if(Locked)return;Hide();await host.Hide(Kind);};
            KeyDown+=async(s,e)=>{if(e.Key==Key.Escape&&!Locked){Hide();await host.Hide(Kind);}};
        }
        public void SetLocked(bool locked){Locked=locked;dragArea.Cursor=locked?Cursors.Arrow:Cursors.SizeAll;dragArea.ToolTip=locked?"小窗已固定；请在仪表盘解除固定":"拖动此处移动小窗";Pin.IsEnabled=!locked;CloseButton.IsEnabled=!locked;Pin.Visibility=CloseButton.Visibility=locked?Visibility.Collapsed:Visibility.Visible;resizeGrip.IsEnabled=!locked;resizeGrip.Visibility=locked?Visibility.Collapsed:Visibility.Visible;resizeGrip.Cursor=locked?Cursors.Arrow:Cursors.SizeNWSE;resizeGrip.ToolTip=locked?"请在仪表盘解除固定后调整大小":"拖动右下角调整大小";ApplyNativeLock();}
        void ApplyNativeLock(){
            var handle=new WindowInteropHelper(this).Handle;if(handle==IntPtr.Zero)return;
            int style=GetWindowLong(handle,ExtendedStyle);
            SetWindowLong(handle,ExtendedStyle,Locked?style|TransparentStyle|NoActivateStyle:style&~(TransparentStyle|NoActivateStyle));
        }
        static Button Button(string label,string hint){return new Button{Content=label,ToolTip=hint,Background=Brushes.Transparent,BorderThickness=new Thickness(0),Foreground=Data.Muted,Padding=new Thickness(8,1,8,1),FontSize=11,Cursor=Cursors.Hand};}
        public void Update(IDictionary<string,object> state){
            if(Kind=="current"){
                Value.Text=Data.Flag(state,"valid")?Math.Round(Data.Number(state,"bpm")).ToString():"—";
                Value.Foreground=Data.Flag(state,"valid")?Data.HeartRed:Data.Muted;
                Status.Text=Data.Text(state,"status");object age=Data.Get(state,"eventAgeSeconds");Age.Text=age==null?"尚无心率事件":"上次事件："+Math.Floor(Convert.ToDouble(age)).ToString()+" 秒前";
            }else{Chart.Update(state);Age.Text=Data.Text(state,"status")+" · 电脑接收时间";}
            if(host.LayoutError!=null){Age.Text+=" · 布局未保存";Age.ToolTip=host.LayoutError;}else Age.ToolTip=null;
        }
        public void Unavailable(){if(Value!=null){Value.Text="—";Value.Foreground=Data.Muted;Status.Text="本地仪表盘服务暂不可用";}Age.Text="等待连接恢复";}
    }

    public class Host : Application {
        readonly HttpClient client;readonly JavaScriptSerializer json=new JavaScriptSerializer{MaxJsonLength=4000000};
        readonly string baseUrl,layoutPath;readonly Process owner;
        public WidgetWindow CurrentWidget,TrendWidget;public bool Exiting;bool busy;DispatcherTimer timer;
        class LayoutUpdate {public long Sequence;public Dictionary<string,object> Changes;public int Attempts;public DateTime RetryAt;}
        readonly Dictionary<string,LayoutUpdate> pendingLayouts=new Dictionary<string,LayoutUpdate>();
        readonly string layoutClient=Guid.NewGuid().ToString("N");long layoutSequence;bool layoutBusy;
        readonly Func<Dictionary<string,object>,Task<Dictionary<string,object>>> layoutSender;
        public string LayoutError {get;private set;}
        public int PendingLayouts {get{return pendingLayouts.Count;}}
        public Host(int port,int ownerPid,string layout,Func<Dictionary<string,object>,Task<Dictionary<string,object>>> sender=null){baseUrl="http://127.0.0.1:"+port;layoutPath=layout;owner=Process.GetProcessById(ownerPid);client=new HttpClient(new HttpClientHandler{UseProxy=false});client.Timeout=TimeSpan.FromSeconds(2);client.DefaultRequestHeaders.Add("X-HeartRadio-Action","widgets");ShutdownMode=ShutdownMode.OnExplicitShutdown;layoutSender=sender??SendLayout;}
        protected override void OnStartup(StartupEventArgs e){base.OnStartup(e);CurrentWidget=new WidgetWindow(this,"current");TrendWidget=new WidgetWindow(this,"trend");RestoreLayout();timer=new DispatcherTimer{Interval=TimeSpan.FromSeconds(1)};timer.Tick+=Tick;timer.Start();Tick(null,EventArgs.Empty);}
        async void Tick(object sender,EventArgs e){
            if(owner.HasExited){Exiting=true;Shutdown();return;}if(busy)return;busy=true;
            try{
                var state=json.Deserialize<Dictionary<string,object>>(await client.GetStringAsync(baseUrl+"/api/widget-state"));
                var control=(IDictionary<string,object>)Data.Get(state,"widgets");
                var locked=Data.Get(control,"locked") as IDictionary<string,object>;
                if(locked!=null){if(CurrentWidget.Locked!=Data.Flag(locked,"current"))CurrentWidget.SetLocked(Data.Flag(locked,"current"));if(TrendWidget.Locked!=Data.Flag(locked,"trend"))TrendWidget.SetLocked(Data.Flag(locked,"trend"));}
                SetVisible(CurrentWidget,Data.Flag(control,"current"));SetVisible(TrendWidget,Data.Flag(control,"trend"));
                await FlushLayoutAsync();CurrentWidget.Update(state);TrendWidget.Update(state);
            }catch(Exception error){CurrentWidget.Unavailable();TrendWidget.Unavailable();try{File.WriteAllText(Path.Combine(Path.GetDirectoryName(layoutPath),"widgets-error.txt"),error.ToString(),Encoding.UTF8);}catch{}}finally{busy=false;}
        }
        void SetVisible(Window window,bool visible){if(visible&&!window.IsVisible)window.Show();else if(!visible&&window.IsVisible)window.Hide();}
        public async Task Hide(string kind){
            SaveLayout(kind);await FlushLayoutAsync();
            try{var body=json.Serialize(new Dictionary<string,object>{{"widget",kind},{"visible",false}});var response=await client.PostAsync(baseUrl+"/api/widgets",new StringContent(body,Encoding.UTF8,"application/json"));response.EnsureSuccessStatusCode();}catch(Exception){}
        }
        public void SaveLayout(string kind=null){
            if(CurrentWidget==null||TrendWidget==null)return;
            foreach(var w in new[]{CurrentWidget,TrendWidget}){
                if((kind!=null&&kind!=w.Kind)||w.Locked)continue;
                pendingLayouts[w.Kind]=new LayoutUpdate{Sequence=++layoutSequence,Changes=new Dictionary<string,object>{{"left",w.Left},{"top",w.Top},{"width",w.Width},{"height",w.Height},{"topmost",w.Topmost}}};
            }
            LayoutError=null;
            // Save on completed gestures only. Tick retries a failed write; mouse movement never does I/O.
            var ignored=FlushLayoutAsync();
        }
        async Task<Dictionary<string,object>> SendLayout(Dictionary<string,object> value){
            var response=await client.PostAsync(baseUrl+"/api/widgets",new StringContent(json.Serialize(value),Encoding.UTF8,"application/json"));
            response.EnsureSuccessStatusCode();return json.Deserialize<Dictionary<string,object>>(await response.Content.ReadAsStringAsync());
        }
        public async Task FlushLayoutAsync(){
            if(layoutBusy)return;layoutBusy=true;
            try{
                foreach(var kind in new[]{"current","trend"}){
                    LayoutUpdate entry;if(!pendingLayouts.TryGetValue(kind,out entry)||entry.Attempts>=3||entry.RetryAt>DateTime.UtcNow)continue;
                    try{
                        var reply=await layoutSender(new Dictionary<string,object>{{"widget",kind},{"layout",entry.Changes},{"client_id",layoutClient},{"sequence",entry.Sequence}});
                        string status=Data.Text(reply,"status");if(status!="saved"&&status!="already_applied"&&status!="locked")throw new IOException("布局保存尚未确认");
                        LayoutUpdate current;if(pendingLayouts.TryGetValue(kind,out current)&&current.Sequence==entry.Sequence)pendingLayouts.Remove(kind);
                        if(status=="locked"){var window=kind=="current"?CurrentWidget:TrendWidget;window.SetLocked(true);}
                        LayoutError=null;
                    }catch(Exception){entry.Attempts++;entry.RetryAt=DateTime.UtcNow.AddSeconds(entry.Attempts*2);}
                }
            }finally{layoutBusy=false;LayoutError=null;foreach(var entry in pendingLayouts.Values)if(entry.Attempts>0)LayoutError="当前位置暂未保存，连接恢复后重新调整即可再次保存。";}
        }
        void RestoreLayout(){
            try{if(!File.Exists(layoutPath))return;var all=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(layoutPath));foreach(var w in new[]{CurrentWidget,TrendWidget}){var d=Data.Get(all,w.Kind)as IDictionary<string,object>;if(d==null)continue;w.Width=Math.Max(w.MinWidth,Math.Min(w.MaxWidth,Data.Number(d,"width",w.Width)));w.Height=Math.Max(w.MinHeight,Math.Min(w.MaxHeight,Data.Number(d,"height",w.Height)));w.Left=Math.Max(SystemParameters.VirtualScreenLeft,Math.Min(Data.Number(d,"left",w.Left),SystemParameters.VirtualScreenLeft+SystemParameters.VirtualScreenWidth-w.Width));w.Top=Math.Max(SystemParameters.VirtualScreenTop,Math.Min(Data.Number(d,"top",w.Top),SystemParameters.VirtualScreenTop+SystemParameters.VirtualScreenHeight-w.Height));w.Topmost=Data.Flag(d,"topmost");w.Pin.Content=w.Topmost?"置顶 ✓":"置顶";w.SetLocked(Data.Get(d,"locked")==null?Data.Flag(d,"movementLocked"):Data.Flag(d,"locked"));}}
            catch(Exception){}
        }
        protected override void OnExit(ExitEventArgs e){Exiting=true;var ignored=FlushLayoutAsync();if(timer!=null)timer.Stop();client.Dispose();base.OnExit(e);}
        [STAThread] public static void Main(string[] args){
            try{if(args.Length!=3)throw new ArgumentException("请从HeartRadio网页开启桌面小窗");new Host(Int32.Parse(args[0]),Int32.Parse(args[1]),args[2]).Run();}
            catch(Exception error){try{string folder=Path.GetDirectoryName(args.Length==3?args[2]:AppDomain.CurrentDomain.BaseDirectory);File.WriteAllText(Path.Combine(folder,"widgets-error.txt"),error.ToString(),Encoding.UTF8);}catch{} }
        }
    }
}

