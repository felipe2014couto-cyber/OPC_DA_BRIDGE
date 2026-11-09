# Diagnostic helper for PI OPC Client 1.4.0.0 and RustDaBridge.Lab.1 only.
# Uses the existing Windows/.NET runtime; installs nothing and writes no registry.
# Default: attach in the same desktop/session. -StartDiagnosticClient creates and
# cleans up its own PI client and newly launched lab server processes.
param(
    [switch]$StartDiagnosticClient,
    [int]$ClientProcessId = 0,
    [string]$ClientPath = 'D:\AppData\PIPC\PI OPC Tools\PI_OPCClient\OPCClient.exe',
    [string]$LabServerPath = 'D:\OPCB\rust-da-com-lab-20261105-diag-build8\rust-da-com-lab-diag.exe'
)
$ErrorActionPreference = 'Stop'
if ([IntPtr]::Size -ne 4) { throw 'Run 32-bit Windows PowerShell: target client is x86.' }
Add-Type -TypeDefinition @'

using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class LabUI {
 public delegate bool EnumProc(IntPtr h, IntPtr p);
 [DllImport("user32.dll")] static extern bool EnumThreadWindows(uint t,EnumProc cb,IntPtr p);
 [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h,EnumProc cb,IntPtr p);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h,StringBuilder s,int n);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h,StringBuilder s,int n);
 [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetDlgItem(IntPtr h,int id);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
 [DllImport("user32.dll")] static extern int GetDlgCtrlID(IntPtr h);
 [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h,uint m,IntPtr w,StringBuilder l);
 public static string Describe(IntPtr h) { StringBuilder t=new StringBuilder(2048);StringBuilder c=new StringBuilder(256);GetWindowText(h,t,t.Capacity);GetClassName(h,c,c.Capacity);uint p;GetWindowThreadProcessId(h,out p);return h.ToInt64()+"|pid="+p+"|id="+GetDlgCtrlID(h)+"|"+c+"|visible="+IsWindowVisible(h)+"|enabled="+IsWindowEnabled(h)+"|"+t; }
 public static string[] Windows(int pid) { List<string> a=new List<string>();foreach(System.Diagnostics.ProcessThread t in System.Diagnostics.Process.GetProcessById(pid).Threads){EnumThreadWindows((uint)t.Id,delegate(IntPtr h,IntPtr p){a.Add(Describe(h));EnumChildWindows(h,delegate(IntPtr c,IntPtr x){a.Add("  "+Describe(c));return true;},IntPtr.Zero);return true;},IntPtr.Zero);}return a.ToArray();}
 [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr OpenProcess(uint a,bool inherit,uint pid);
 [DllImport("kernel32.dll",SetLastError=true)] static extern IntPtr VirtualAllocEx(IntPtr p,IntPtr a,UIntPtr n,uint type,uint protect);
 [DllImport("kernel32.dll")] static extern bool VirtualFreeEx(IntPtr p,IntPtr a,UIntPtr n,uint type);
 [DllImport("kernel32.dll")] static extern bool WriteProcessMemory(IntPtr p,IntPtr a,byte[] b,UIntPtr n,out UIntPtr written);
 [DllImport("kernel32.dll")] static extern bool ReadProcessMemory(IntPtr p,IntPtr a,byte[] b,UIntPtr n,out UIntPtr read);
 [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
 [StructLayout(LayoutKind.Sequential)] struct TVITEM { public uint mask;public IntPtr item;public uint state;public uint stateMask;public IntPtr text;public int max;public int image;public int selected;public int children;public IntPtr param; }
 public static string Text(IntPtr h) { StringBuilder b=new StringBuilder(4096);SendMessage(h,13,new IntPtr(b.Capacity),b);return b.ToString(); }
 public static string TreeText(IntPtr h,IntPtr item) {uint pid;GetWindowThreadProcessId(h,out pid);IntPtr p=OpenProcess(0x438,false,pid);if(p==IntPtr.Zero)throw new Exception("OpenProcess failed");IntPtr mem=VirtualAllocEx(p,IntPtr.Zero,new UIntPtr(4096),0x3000,4);try{TVITEM t=new TVITEM();t.mask=1;t.item=item;t.text=new IntPtr(mem.ToInt64()+128);t.max=1024;int n=Marshal.SizeOf(typeof(TVITEM));IntPtr local=Marshal.AllocHGlobal(n);byte[] bytes=new byte[n];try{Marshal.StructureToPtr(t,local,false);Marshal.Copy(local,bytes,0,n);}finally{Marshal.FreeHGlobal(local);}UIntPtr used;WriteProcessMemory(p,mem,bytes,new UIntPtr((uint)n),out used);SendMessage(h,0x113e,IntPtr.Zero,mem);byte[] text=new byte[2048];ReadProcessMemory(p,t.text,text,new UIntPtr(2048),out used);return Encoding.Unicode.GetString(text).Split('\0')[0];}finally{VirtualFreeEx(p,mem,UIntPtr.Zero,0x8000);CloseHandle(p);} }
 static void WalkTree(IntPtr h,IntPtr item,List<string> a,int depth){int count=0;while(item!=IntPtr.Zero&&count++<100&&depth<16){a.Add(item.ToInt64()+"|"+TreeText(h,item));IntPtr child=SendMessage(h,0x110a,new IntPtr(4),item);WalkTree(h,child,a,depth+1);item=SendMessage(h,0x110a,new IntPtr(1),item);} }
 public static string[] Tree(IntPtr h){List<string>a=new List<string>();WalkTree(h,SendMessage(h,0x110a,IntPtr.Zero,IntPtr.Zero),a,0);return a.ToArray();}

 [StructLayout(LayoutKind.Sequential)] struct LVITEM {public uint mask;public int row;public int col;public uint state;public uint stateMask;public IntPtr text;public int max;public int image;public IntPtr param;public int indent;}
 static IntPtr RemoteStruct(IntPtr p,IntPtr mem,object value){int n=Marshal.SizeOf(value);IntPtr local=Marshal.AllocHGlobal(n);byte[] b=new byte[n];try{Marshal.StructureToPtr(value,local,false);Marshal.Copy(local,b,0,n);}finally{Marshal.FreeHGlobal(local);}UIntPtr used;if(!WriteProcessMemory(p,mem,b,new UIntPtr((uint)n),out used))throw new Exception("WriteProcessMemory");return mem;}
 public static string Cell(IntPtr h,int row,int col){uint pid;GetWindowThreadProcessId(h,out pid);IntPtr p=OpenProcess(0x438,false,pid);if(p==IntPtr.Zero)throw new Exception("OpenProcess");IntPtr mem=VirtualAllocEx(p,IntPtr.Zero,new UIntPtr(8192),0x3000,4);try{LVITEM t=new LVITEM();t.mask=1;t.row=row;t.col=col;t.text=new IntPtr(mem.ToInt64()+128);t.max=2048;RemoteStruct(p,mem,t);SendMessage(h,0x1073,new IntPtr(row),mem);byte[] b=new byte[4096];UIntPtr used;ReadProcessMemory(p,t.text,b,new UIntPtr(4096),out used);return Encoding.Unicode.GetString(b).Split('\0')[0];}finally{VirtualFreeEx(p,mem,UIntPtr.Zero,0x8000);CloseHandle(p);} }
 public static string[] List(IntPtr h){List<string>a=new List<string>();int rows=SendMessage(h,0x1004,IntPtr.Zero,IntPtr.Zero).ToInt32();IntPtr head=SendMessage(h,0x101f,IntPtr.Zero,IntPtr.Zero);int cols=SendMessage(head,0x1200,IntPtr.Zero,IntPtr.Zero).ToInt32();for(int r=0;r<Math.Min(rows,100);r++){string line="row="+r+" state="+SendMessage(h,0x102c,new IntPtr(r),new IntPtr(3));for(int c=0;c<Math.Min(cols,12);c++)line+="|"+Cell(h,r,c);a.Add(line);}return a.ToArray();}

 public static int Owner(IntPtr h){uint pid;GetWindowThreadProcessId(h,out pid);return (int)pid;}
 [DllImport("user32.dll",CharSet=CharSet.Unicode,EntryPoint="SendMessageW")] static extern IntPtr SendText(IntPtr h,uint m,IntPtr w,string text);
 public static void SetText(IntPtr h,string text){SendText(h,12,IntPtr.Zero,text);}

}
'@

function Assert-Client {
    $proc = Get-WmiObject Win32_Process -Filter ("ProcessId=" + $client.Id)
    if (!$proc -or $proc.ExecutablePath -ne $ClientPath -or $proc.SessionId -ne $sessionId) {
        throw 'PI client PID/path/session changed; refusing GUI operations.'
    }
}
function Dialog([string]$title) {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        $line = [LabUI]::Windows($client.Id) | Where-Object {
            $_ -match ('\|#32770\|.*\|' + [Regex]::Escape($title) + '$')
        } | Select-Object -First 1
        if ($line) { return [IntPtr][long]($line.Split('|')[0]) }
        Start-Sleep -Milliseconds 200
    }
    throw "Dialog '$title' unavailable. Run Attach in the same RDP session as PI OPC Client."
}
function Click([IntPtr]$dialog, [int]$control) {
    Assert-Client
    $button = [LabUI]::GetDlgItem($dialog, $control)
    if ([LabUI]::Owner($dialog) -ne $client.Id -or !$button -or
        ![LabUI]::IsWindowEnabled($button)) {
        throw "Control $control is disabled or not owned by the verified PI client."
    }
    # Queued WM_COMMAND reaches the same handler as BN_CLICKED. Using a
    # synchronous cross-process command can prohibit an outgoing STA COM call.
    [LabUI]::PostMessage($dialog, 273, [IntPtr]$control, $button) | Out-Null
    Start-Sleep -Milliseconds 700
}
function Assert-LabServer([IntPtr]$main) {
    $tree = [LabUI]::GetDlgItem($main, 1000)
    $selected = [LabUI]::SendMessage($tree, 0x110a, [IntPtr]9, [IntPtr]::Zero)
    if ([LabUI]::TreeText($tree, $selected) -ne 'RustDaBridge.Lab.1') {
        throw 'Select only RustDaBridge.Lab.1 in the OPC Servers tree.'
    }
}
$sessionId = (Get-Process -Id $PID).SessionId
$beforeServers = @(Get-WmiObject Win32_Process | Where-Object {
    $_.ExecutablePath -eq $LabServerPath
} | ForEach-Object { $_.ProcessId })
$client = $null
try {
    if ([Diagnostics.FileVersionInfo]::GetVersionInfo($ClientPath).FileVersion -ne '1.4.0.0') {
        throw 'This helper has only been validated with PI OPC Client 1.4.0.0.'
    }
    $key = [Microsoft.Win32.Registry]::ClassesRoot.OpenSubKey(
        'CLSID\{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}\LocalServer32')
    if (!$key -or ([string]$key.GetValue('')).Trim('"') -ne $LabServerPath) {
        throw 'Effective lab LocalServer32 does not match LabServerPath.'
    }
    $key.Close()
    if ($StartDiagnosticClient) {
        if (Get-WmiObject Win32_Process | Where-Object {
            $_.ExecutablePath -eq $ClientPath -and $_.SessionId -eq $sessionId
        }) { throw 'A PI client already exists in this session; use Attach or close it first.' }
        $client = Start-Process -FilePath $ClientPath -WorkingDirectory (Split-Path $ClientPath) -PassThru
        Start-Sleep -Seconds 2
    } else {
        $candidates = @(Get-WmiObject Win32_Process | Where-Object {
            $_.ExecutablePath -eq $ClientPath -and $_.SessionId -eq $sessionId -and
            (!$ClientProcessId -or $_.ProcessId -eq $ClientProcessId)
        })
        if ($candidates.Count -ne 1) { throw 'Exactly one PI client is required in this session.' }
        $client = Get-Process -Id $candidates[0].ProcessId
    }
    Assert-Client
    'PI_CLIENT pid=' + $client.Id + ' session=' + $sessionId + ' version=1.4.0.0'
    $main = Dialog 'PI_OPCClient'
    if ($StartDiagnosticClient) {
        [LabUI]::SetText([LabUI]::GetDlgItem($main, 1009), 'Localhost')
        Click $main 1011
        $tree = [LabUI]::GetDlgItem($main, 1000)
        $entry = @([LabUI]::Tree($tree) | Where-Object { $_ -match '\|RustDaBridge\.Lab\.1$' })
        if ($entry.Count -ne 1) { throw 'Expected exactly one registered Rust lab server.' }
        $node = [IntPtr][long]($entry[0].Split('|')[0])
        [LabUI]::SendMessage($tree, 0x110b, [IntPtr]9, $node) | Out-Null
        Start-Sleep -Milliseconds 200
        Assert-LabServer $main
        Click $main 1022
        $warning = Dialog 'Warning!'
        $warningText = [LabUI]::Text([LabUI]::GetDlgItem($warning, 65535))
        if ($warningText -ne 'Shutdown notification not supported by this server') {
            throw "Unexpected PI connection warning: $warningText"
        }
        Click $warning 2
    }
    Assert-LabServer $main
    Click $main 1015
    $properties = Dialog 'OPC Server Properties'
    $interfaces = [LabUI]::GetDlgItem($properties, 1053)
    $syncSupported = $false
    $rows = [LabUI]::SendMessage($interfaces, 0x1004, [IntPtr]::Zero, [IntPtr]::Zero).ToInt32()
    for ($row = 0; $row -lt $rows; $row++) {
        if ([LabUI]::Cell($interfaces, $row, 0) -eq 'IOPCSyncIO') {
            $da1 = [LabUI]::Cell($interfaces, $row, 1)
            $da2 = [LabUI]::Cell($interfaces, $row, 2)
            'PI_INTERFACE IOPCSyncIO DA1=' + $da1 + ' DA2=' + $da2
            $syncSupported = $da2.StartsWith('Yes')
        }
    }
    [LabUI]::PostMessage($properties, 16, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
    Start-Sleep -Milliseconds 500
    if (!$syncSupported) { throw 'PI client reports IOPCSyncIO unsupported.' }
    if ($StartDiagnosticClient) {
        Click $main 1019
        $group = Dialog 'Add Group'
        # Use the installed client defaults: active Group1, 1000 ms, DA v2.05a.
        $version = [LabUI]::Text([LabUI]::GetDlgItem($group, 1032))
        $active = [LabUI]::SendMessage([LabUI]::GetDlgItem($group, 1014), 0xf0, [IntPtr]::Zero, [IntPtr]::Zero)
        if ($version -ne 'v2.05a' -or $active.ToInt32() -ne 1) {
            throw 'Expected active group and DA v2.05a defaults.'
        }
        Click $group 1
        Click $main 1016
        $add = Dialog 'Add Item'
        [LabUI]::SendMessage([LabUI]::GetDlgItem($add, 1032), 0xf1, [IntPtr]1, [IntPtr]::Zero) | Out-Null
        Click $add 1032
        Click $add 1019
        $browse = [LabUI]::GetDlgItem($add, 1002)
        $count = [LabUI]::SendMessage($browse, 0x1004, [IntPtr]::Zero, [IntPtr]::Zero).ToInt32()
        $found = @()
        for ($row = 0; $row -lt $count; $row++) { $found += [LabUI]::Cell($browse, $row, 1) }
        if ($count -ne 2 -or
            !($found -contains 'RustLab.Boolean') -or
            !($found -contains 'RustLab.Numeric.Double')) { throw 'Unexpected browse items; refusing Add Selected.' }
        Click $add 1037
        Click $add 1033
        Click $add 1
    }
    Assert-LabServer $main
    $groups = [LabUI]::GetDlgItem($main, 1003)
    $items = [LabUI]::GetDlgItem($main, 1002)
    $groupSelected = [LabUI]::SendMessage($groups, 0x1032, [IntPtr]::Zero, [IntPtr]::Zero).ToInt32()
    $itemCount = [LabUI]::SendMessage($items, 0x1004, [IntPtr]::Zero, [IntPtr]::Zero).ToInt32()
    $itemSelected = [LabUI]::SendMessage($items, 0x1032, [IntPtr]::Zero, [IntPtr]::Zero).ToInt32()
    'SELECTION groups=' + $groupSelected + ' items=' + $itemCount + ' selectedItems=' + $itemSelected
    if ($groupSelected -ne 1 -or $itemCount -ne 2 -or ($itemSelected -ne 0 -and $itemSelected -ne 2)) {
        throw 'Select one group containing both lab tags; select both item rows or leave both unselected.'
    }
    $found = @([LabUI]::Cell($items, 0, 5), [LabUI]::Cell($items, 1, 5))
    if (!($found -contains 'RustLab.Boolean') -or !($found -contains 'RustLab.Numeric.Double')) {
        throw 'Item list is not exclusively the two simulated Rust lab tags.'
    }
    $sourceButton = [LabUI]::GetDlgItem($main, 1029)
    if ([LabUI]::Text($sourceButton) -eq 'Cache') { Click $main 1029 }
    if ([LabUI]::Text($sourceButton) -ne 'Dev') { throw 'Device source not selected.' }
    $logPath = Join-Path (Split-Path $LabServerPath) 'rust-da-com-lab.log'
    $before = @(Get-Content $logPath).Count
    'ACTION=SyncRead control=1028 source=Device'
    Click $main 1028
    $new = @()
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $new = @(Get-Content $logPath | Select-Object -Skip $before)
        if ($new -match '\[COM_SYNCIO\].*Read RETURN hresult=S_OK') { break }
        Start-Sleep -Milliseconds 250
    }
    $entry = @($new | Where-Object { $_ -match '\[COM_SYNCIO\].*Read ENTER source=2 count=2' })
    $returned = @($new | Where-Object { $_ -match '\[COM_SYNCIO\].*Read RETURN hresult=S_OK' })
    $results = @($new | Where-Object { $_ -match '\[READ_RESULT\].*result=S_OK' })
    $new | Where-Object { $_ -match '\[COM_SYNCIO\]|\[READ_REQUEST\]|\[READ_RESULT\]' }
    if ($entry.Count -ne 1 -or $returned.Count -ne 1 -or $results.Count -ne 2) {
        [LabUI]::Windows($client.Id) | Where-Object { $_ -match '#32770|65535\|Static' }
        throw 'PI SyncRead did not produce one two-item method call; inspect this run only.'
    }
    $serverId = [int]([Regex]::Match($entry[0], 'pid=(\d+)').Groups[1].Value)
    $server = Get-WmiObject Win32_Process -Filter ("ProcessId=" + $serverId)
    if ($server.ExecutablePath -ne $LabServerPath) { throw 'Read server executable mismatch.' }
    'VERIFIED_SERVER pid=' + $serverId + ' path=' + $server.ExecutablePath
    Start-Sleep -Milliseconds 300
    for ($row = 0; $row -lt 2; $row++) {
        $id = [LabUI]::Cell($items, $row, 5)
        $value = [LabUI]::Cell($items, $row, 1)
        $quality = [LabUI]::Cell($items, $row, 2)
        $type = [LabUI]::Cell($items, $row, 4)
        'PI_UI id=' + $id + ' value=' + $value + ' quality=' + $quality + ' type=' + $type
        if (!$value -or !$quality.StartsWith('Good')) { throw 'PI UI did not display Good data.' }
    }
    'PI_CLIENT_SYNC_PASS write=not_called advise=not_called'
} finally {
    if ($StartDiagnosticClient -and $client) {
        $proc = Get-WmiObject Win32_Process -Filter ("ProcessId=" + $client.Id)
        if ($proc -and $proc.ExecutablePath -eq $ClientPath -and $proc.SessionId -eq $sessionId) {
            'CLEANUP_PI pid=' + $proc.ProcessId + ' path=' + $proc.ExecutablePath
            Stop-Process -Id $proc.ProcessId -Force
        }
        Get-WmiObject Win32_Process | Where-Object {
            $_.ExecutablePath -eq $LabServerPath -and $_.SessionId -eq $sessionId -and
            $_.CommandLine -match ' -Embedding(\s|$)' -and
            $beforeServers -notcontains $_.ProcessId
        } | ForEach-Object {
            'CLEANUP_LAB pid=' + $_.ProcessId + ' path=' + $_.ExecutablePath
            Stop-Process -Id $_.ProcessId -Force
        }
    }
}
