// Windows .NET Framework launcher. Build as a WindowsApplication; no console required.
using System;
using System.Collections.Generic;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Windows.Forms;

internal sealed class RealmLauncher : Form {
    readonly string root = AppDomain.CurrentDomain.BaseDirectory;
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly TextBox server = new TextBox(), password = new TextBox(), name = new TextBox(), output = new TextBox();
    readonly Button start = new Button(), check = new Button(), stop = new Button();
    readonly CheckBox useGse = new CheckBox();
    readonly Label status = new Label();
    readonly Timer timer = new Timer();
    readonly ConcurrentQueue<string> lines = new ConcurrentQueue<string>();
    Process runner;
    string cancelPath;
    bool closeAfter;

    public RealmLauncher(bool smoke) {
        SuspendLayout();
        AutoScaleDimensions = new SizeF(96F,96F);
        AutoScaleMode = AutoScaleMode.Dpi;
        Text = "恶意不息 · 共享国度";
        ClientSize = new Size(620, 584);
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        Font = new Font("Microsoft YaHei UI", 10);
        BackColor = Color.FromArgb(246,247,250);
        Label title = LabelAt("连接你的共享国度",32,23,550,38);
        title.Font = new Font("Microsoft YaHei UI",17,FontStyle.Bold);
        title.SetBounds(32,23,550,38);
        LabelAt("v29466  /  最多 4 人  /  角色与国度保存在服务器",32,65,550,26);
        LabelAt("服务器地址",32,113,120,28); InputAt(server,110,255);
        LabelAt("联机密码",32,158,120,28); InputAt(password,155,256); password.UseSystemPasswordChar = true;
        LabelAt("Steam ID",32,203,120,28); InputAt(name,200,17);
        useGse.Text="使用随包 GSE 兼容层（模拟版；首次安装会备份原 DLL）";
        useGse.Checked=true; useGse.SetBounds(32,244,548,28); Controls.Add(useGse);
        Label note = LabelAt("服务器可填 IP:端口，默认 TLS 端口 8443。Steam ID 为 17 位数字。正版可取消 GSE 选项，使用当前 Steam 身份。",32,278,548,45);
        note.ForeColor = Color.FromArgb(80,87,103);
        ButtonAt(start,"连接并启动游戏",32,220);
        ButtonAt(check,"检查连接",265,145);
        ButtonAt(stop,"停止",423,157); stop.Enabled = false;
        status.Text = "请输入联机密码。游戏运行期间请保持启动器打开。";
        Controls.Add(status); status.SetBounds(32,393,548,30);
        output.SetBounds(32,430,548,125);
        output.Multiline = true; output.ReadOnly = true; output.ScrollBars = ScrollBars.Vertical;
        output.Font = new Font("Consolas",9); Controls.Add(output);
        AcceptButton = start;
        LoadDefaults();
        start.Click += delegate { StartClient(false); };
        check.Click += delegate { StartClient(true); };
        stop.Click += delegate { RequestStop(); };
        FormClosing += delegate(object sender, FormClosingEventArgs e) {
            if (runner != null) { e.Cancel = true; closeAfter = true; RequestStop(); }
        };
        timer.Interval = 150; timer.Tick += delegate { Poll(); };
        if (smoke) {
            Timer smokeTimer = new Timer(); smokeTimer.Interval = 800;
            smokeTimer.Tick += delegate { smokeTimer.Stop(); smokeTimer.Dispose(); Close(); };
            Shown += delegate {
                using(Graphics graphics=CreateGraphics()) {
                    var clipped=new List<string>();
                    var controls=new List<object>();
                    foreach(Control control in Controls) {
                        controls.Add(new {type=control.GetType().Name,x=control.Left,y=control.Top,width=control.Width,height=control.Height,fontSize=control.Font.Size});
                        if(control.Right>ClientSize.Width || control.Bottom>ClientSize.Height)
                            clipped.Add(control.Name+":"+control.GetType().Name);
                    }
                    Directory.CreateDirectory(Path.Combine(root,"logs"));
                    File.WriteAllText(Path.Combine(root,"logs/launcher-dpi-check.json"),json.Serialize(new {
                        dpi=graphics.DpiX, awareness=WindowDpiAwareness(Handle),
                        clientWidth=ClientSize.Width, clientHeight=ClientSize.Height,
                        clippedControls=clipped, controls=controls
                    }),Encoding.UTF8);
                }
                smokeTimer.Start();
            };
        }
        ResumeLayout(true);
    }
    Label LabelAt(string text,int x,int y,int width,int height) {
        Label label = new Label { Text=text }; Controls.Add(label); label.SetBounds(x,y,width,height); return label;
    }
    void InputAt(TextBox input,int y,int max) {
        input.SetBounds(155,y,425,30); input.MaxLength=max; Controls.Add(input);
    }
    void ButtonAt(Button button,string text,int x,int width) {
        button.Text=text; button.SetBounds(x,336,width,42); Controls.Add(button);
    }
    Dictionary<string,object> ReadObject(string relative) {
        string path=Path.Combine(root,relative);
        return File.Exists(path) ? json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path,Encoding.UTF8)) : new Dictionary<string,object>();
    }
    static string Value(Dictionary<string,object> obj,string key) {
        object val; return obj.TryGetValue(key,out val) && val!=null ? Convert.ToString(val) : "";
    }
    void LoadDefaults() {
        try {
            var saved=ReadObject("data/launcher-settings.json");
            if(Value(saved,"server")!="")server.Text=Value(saved,"server");
            if(Value(saved,"steam_mode")=="native")useGse.Checked=false;
            name.Text=Value(saved,"steam_id");
            string game=Directory.GetParent(root.TrimEnd(Path.DirectorySeparatorChar)).FullName;
            string config=Path.Combine(game,"NoRestForTheWicked_Data/Plugins/x86_64/steam_settings/configs.user.ini");
            if(File.Exists(config)) {
                Match identity=Regex.Match(File.ReadAllText(config,Encoding.UTF8),@"(?m)^\s*account_steamid\s*=\s*([0-9]{17})\s*$");
                if(identity.Success)name.Text=identity.Groups[1].Value;
            }
        } catch(Exception e) { status.Text="配置读取失败，请手动填写。"; Append(e.Message); }
    }
    void Append(string line) {
        if(output.TextLength>30000)output.Text=output.Text.Substring(output.TextLength-18000);
        output.AppendText(line+Environment.NewLine);
        if(line.StartsWith("Steam identity: "))name.Text=line.Substring("Steam identity: ".Length).Trim();
        if(line.StartsWith("Signed in: ") || line.StartsWith("Created account: "))status.Text="已登录。启动游戏时会校验客户端版本，请稍候。";
        if(line.Contains("\"event\": \"launched\""))status.Text="游戏已启动。请保持启动器打开。";
    }
    void SetRunning(bool active) {
        start.Enabled=check.Enabled=server.Enabled=password.Enabled=name.Enabled=useGse.Enabled=!active;
        stop.Enabled=active;
    }
    void StartClient(bool checkOnly) {
        if(runner!=null)return;
        if(server.Text.Trim()=="" || name.Text.Trim()=="" || password.Text=="") {
            status.Text="请填写服务器地址、联机密码和 Steam ID。"; return;
        }
        if(!Regex.IsMatch(name.Text.Trim(),@"^[0-9]{17}$")) { status.Text="Steam ID 必须为 17 位数字。"; return; }
        try {
            string python=Path.Combine(root,"runtime/python.exe");
            if(!File.Exists(python))python=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),"miniconda3/python.exe");
            if(!File.Exists(python))throw new Exception("找不到 Python 运行环境，请使用完整启动器包。");
            Directory.CreateDirectory(Path.Combine(root,"data"));
            File.WriteAllText(Path.Combine(root,"data/launcher-settings.json"),json.Serialize(new {server=server.Text.Trim(),steam_id=name.Text.Trim(),steam_mode=useGse.Checked?"gse":"native"}),new UTF8Encoding(false));
            cancelPath=Path.Combine(root,"data/launcher-cancel-"+Guid.NewGuid().ToString("N"));
            var info=new ProcessStartInfo(python);
            info.Arguments="\""+Path.Combine(root,"client/connect_and_launch.py")+"\" --login-stdin --cancel-file \""+cancelPath+"\""+(checkOnly?" --check":"");
            info.WorkingDirectory=Directory.GetParent(root.TrimEnd(Path.DirectorySeparatorChar)).FullName;
            info.UseShellExecute=false; info.CreateNoWindow=true;
            info.RedirectStandardInput=info.RedirectStandardOutput=info.RedirectStandardError=true;
            info.StandardOutputEncoding=info.StandardErrorEncoding=Encoding.UTF8;
            info.EnvironmentVariables["PYTHONIOENCODING"]="utf-8";
            info.EnvironmentVariables["PYTHONUNBUFFERED"]="1";
            Process process=new Process {StartInfo=info};
            process.OutputDataReceived+=delegate(object sender,DataReceivedEventArgs e) { if(e.Data!=null)lines.Enqueue(e.Data); };
            process.ErrorDataReceived+=delegate(object sender,DataReceivedEventArgs e) { if(e.Data!=null)lines.Enqueue(e.Data); };
            output.Clear();
            process.Start(); runner=process; SetRunning(true);
            process.BeginOutputReadLine(); process.BeginErrorReadLine();
            // The shared password travels over stdin, never in command-line arguments or settings.
            process.StandardInput.WriteLine(AsciiJson(json.Serialize(new {server=server.Text.Trim(),steam_id=name.Text.Trim(),password=password.Text,steam_mode=useGse.Checked?"gse":"native"})));
            process.StandardInput.Close();
            status.Text="正在连接服务器并登录账号……"; timer.Start();
        } catch(Exception e) {
            Append(e.Message); status.Text="启动失败，请查看下方信息。";
            if(runner==null)SetRunning(false); else { RequestStop(); timer.Start(); }
        }
    }
    static string AsciiJson(string text) {
        // .NET Framework's child-input encoding can follow the system code page.
        // JSON escapes keep names/passwords lossless on all Windows locales.
        StringBuilder ascii=new StringBuilder();
        foreach(char c in text) { if(c>127)ascii.Append("\\u"+((int)c).ToString("x4")); else ascii.Append(c); }
        return ascii.ToString();
    }
    void RequestStop() {
        if(runner==null)return;
        File.WriteAllText(cancelPath,"stop"); stop.Enabled=false;
        status.Text="正在停止游戏和服务器连接……";
    }
    void Drain() { string line; while(lines.TryDequeue(out line))Append(line); }
    void Poll() {
        Drain();
        if(runner!=null && runner.HasExited) {
            runner.WaitForExit(); Drain(); int code=runner.ExitCode;
            runner.Dispose(); runner=null; timer.Stop(); SetRunning(false);
            status.Text=code==0 ? "连接检查完成，或游戏已退出。" : "连接或游戏启动失败，请查看下方信息。";
            if(File.Exists(cancelPath))File.Delete(cancelPath);
            if(closeAfter)Close();
        }
    }
    [STAThread]
    static void Main(string[] args) {
        EnableDpiAwareness();
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new RealmLauncher(Array.IndexOf(args,"--smoke-test")>=0));
    }
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    [DllImport("shcore.dll")] static extern int SetProcessDpiAwareness(int awareness);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] static extern IntPtr GetWindowDpiAwarenessContext(IntPtr window);
    [DllImport("user32.dll")] static extern int GetAwarenessFromDpiAwarenessContext(IntPtr context);
    static void EnableDpiAwareness() {
        // Must run before any window is created. The embedded manifest is primary;
        // these fallbacks cover older Windows versions and custom hosting.
        try { if(SetProcessDpiAwarenessContext(new IntPtr(-4)))return; }
        catch(EntryPointNotFoundException) { }
        try { if(SetProcessDpiAwareness(2)==0)return; }
        catch(DllNotFoundException) { } catch(EntryPointNotFoundException) { }
        SetProcessDPIAware();
    }
    static int WindowDpiAwareness(IntPtr window) {
        try { return GetAwarenessFromDpiAwarenessContext(GetWindowDpiAwarenessContext(window)); }
        catch(EntryPointNotFoundException) { return -1; }
    }
}
