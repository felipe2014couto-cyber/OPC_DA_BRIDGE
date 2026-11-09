# Internal lab test: PI OPC Client Refresh. Default uses a volatile class object;
# -Registered tests the current LocalServer32 activation without source loss.
param(
    [switch]$Registered,
    [string]$ServerExecutable = 'D:\OPCB\rust-da-com-lab-20261105-shutdown-build1\rust-da-com-lab.exe',
    [string]$ExpectedSha256 = '622fdc4d1e6755f761d77062778ef7feef08a82c231ac3cff35daa459a2ecc5e',
    [string]$ClientPath = 'D:\AppData\PIPC\PI OPC Tools\PI_OPCClient\OPCClient.exe',
    [string]$BooleanItemId = 'RustLab.Boolean',
    [string]$DoubleItemId = 'RustLab.Numeric.Double',
    [string[]]$ExpectedItemIds = @()
)
$ErrorActionPreference='Stop'
if(!$ExpectedItemIds.Count){$ExpectedItemIds=@($BooleanItemId,$DoubleItemId)}
$expectedCount=$ExpectedItemIds.Count
if($expectedCount -lt 2 -or @($ExpectedItemIds|Select-Object -Unique).Count -ne $expectedCount){throw 'Expected at least two unique laboratory IDs.'}
if ([IntPtr]::Size -ne 4) { throw 'Run 32-bit Windows PowerShell.' }
$serverDir=Split-Path $ServerExecutable
$flag=Join-Path $serverDir 'rust-da-source-offline.flag'
$log=Join-Path $serverDir 'rust-da-com-lab.log'
$createdFlag=$false
if(Test-Path $flag){throw 'Source flag already exists; refusing to modify it.'}
$hash=[BitConverter]::ToString(([System.Security.Cryptography.SHA256]::Create()).ComputeHash([IO.File]::ReadAllBytes($ServerExecutable))).Replace('-','').ToLower()
if($hash -ne $ExpectedSha256.ToLower()){throw 'Server hash mismatch.'}
if([Diagnostics.FileVersionInfo]::GetVersionInfo($ClientPath).FileVersion -ne '1.4.0.0'){throw 'PI Client version mismatch.'}
$sessionId=(Get-Process -Id $PID).SessionId
if(@(Get-WmiObject Win32_Process|Where-Object {$_.ExecutablePath -eq $ServerExecutable}).Count){throw 'Server already running; refusing isolated test.'}
if(@(Get-WmiObject Win32_Process|Where-Object {$_.ExecutablePath -eq $ClientPath -and $_.SessionId -eq $sessionId}).Count){throw 'PI Client already running in this session.'}
if($Registered){
 $reg=[Microsoft.Win32.Registry]::ClassesRoot.OpenSubKey('CLSID\{7A2F45B1-64D3-4E8C-9A11-3C852B7F2140}\LocalServer32')
 if(!$reg -or ([string]$reg.GetValue('')).Trim('"') -ne $ServerExecutable){throw 'Registered server path mismatch.'}
 $reg.Close()
}
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

function Dialog([string]$title){
 for($i=0;$i -lt 30;$i++){
  $line=[LabUI]::Windows($client.Id)|Where-Object {$_ -match ('\|#32770\|.*\|'+[Regex]::Escape($title)+'$')}|Select-Object -First 1
  if($line){return [IntPtr][long]($line.Split('|')[0])}
  Start-Sleep -Milliseconds 200
 }
 throw "PI dialog missing: $title"
}
function Click([IntPtr]$dialog,[int]$id){
 $button=[LabUI]::GetDlgItem($dialog,$id)
 if([LabUI]::Owner($dialog) -ne $client.Id -or !$button -or ![LabUI]::IsWindowEnabled($button)){throw "PI control unavailable: $id"}
 [LabUI]::PostMessage($dialog,273,[IntPtr]$id,$button)|Out-Null
 Start-Sleep -Milliseconds 700
}
function VerifyRefresh([string]$phase,[string]$expectedQuality){
 $before=@(Get-Content $log).Count
 Click $main 1025
 $new=@()
 for($attempt=0;$attempt -lt 20;$attempt++){
  $new=@(Get-Content $log|Select-Object -Skip $before|Where-Object {$_ -match ('pid='+$server.Id+' ')})
  if(@($new|Where-Object {$_ -match '\[REFRESH_CALLBACK\].*result=S_OK'}).Count -eq 1){break}
  Start-Sleep -Milliseconds 250
 }
 $results=@($new|Where-Object {$_ -match '\[READ_RESULT\].*result=S_OK'})
 if(@($new|Where-Object {$_ -match ('\[REFRESH_REQUEST\].*source=Cache count='+$expectedCount+'( |$)')}).Count -ne 1 -or $results.Count -ne $expectedCount -or @($new|Where-Object {$_ -match '\[REFRESH_CALLBACK\].*result=S_OK'}).Count -ne 1){
  [LabUI]::Windows($client.Id)|Where-Object {$_ -match '#32770|65535\|Static'}|Out-Host
  throw "Refresh callback failed in phase $phase."
 }
 $times=@()
 foreach($line in $results){
  $m=[Regex]::Match($line,'filetime=(\d+)')
  if(!$m.Success){throw 'Missing source timestamp in server log.'}
  $times+=[UInt64]$m.Groups[1].Value
  $line
 }
 if($times[0] -eq 0 -or @($times|Where-Object {$_ -ne $times[0]}).Count){throw "Unexpected source timestamps in $phase"}
 $script:phaseTimestamp[$phase]=$times[0]
 $items=[LabUI]::GetDlgItem($main,1002)
 for($i=0;$i -lt $expectedCount;$i++){
  $id=[LabUI]::Cell($items,$i,5);$value=[LabUI]::Cell($items,$i,1)
  $quality=[LabUI]::Cell($items,$i,2);$timestamp=[LabUI]::Cell($items,$i,3);$type=[LabUI]::Cell($items,$i,4)
  'PI_ITEM phase='+$phase+' id='+$id+' value='+$value+' quality='+$quality+' timestamp='+$timestamp+' type='+$type
  if(!$quality.StartsWith($expectedQuality) -or !$timestamp){throw "Unexpected PI data in $phase"}
  if(($id -eq $BooleanItemId -and $type -ne 'VT_BOOL') -or ($id -ne $BooleanItemId -and $type -ne 'VT_R8') -or !($ExpectedItemIds -contains $id)){throw "Unexpected PI item/type in $phase"}
 }
}
$client=$null;$server=$null;$phaseTimestamp=@{}
try {
 if(!$Registered){
  $server=Start-Process -FilePath $ServerExecutable -ArgumentList '--volatile-server' -WorkingDirectory $serverDir -PassThru -WindowStyle Hidden
  Start-Sleep -Seconds 2
  $check=Get-WmiObject Win32_Process -Filter ("ProcessId="+$server.Id)
  if(!$check -or $check.ExecutablePath -ne $ServerExecutable -or $check.CommandLine -notmatch ' --volatile-server(\s|$)'){throw 'Unexpected volatile server process.'}
  'TEST_SERVER pid='+$server.Id+' sha256='+$hash
 }
 $client=Start-Process -FilePath $ClientPath -WorkingDirectory (Split-Path $ClientPath) -PassThru
 Start-Sleep -Seconds 2
 'TEST_CLIENT pid='+$client.Id+' session='+$client.SessionId
 $main=Dialog 'PI_OPCClient'
 [LabUI]::SetText([LabUI]::GetDlgItem($main,1009),'Localhost')
 Click $main 1011
 $tree=[LabUI]::GetDlgItem($main,1000)
 $nodes=@([LabUI]::Tree($tree)|Where-Object {$_ -match '\|RustDaBridge\.Lab\.1$'})
 if($nodes.Count -ne 1){throw 'Rust lab ProgID unavailable.'}
 $node=[IntPtr][long]($nodes[0].Split('|')[0])
 [LabUI]::SendMessage($tree,0x110b,[IntPtr]9,$node)|Out-Null
 if([LabUI]::TreeText($tree,[LabUI]::SendMessage($tree,0x110a,[IntPtr]9,[IntPtr]::Zero)) -ne 'RustDaBridge.Lab.1'){throw 'Unexpected selected server.'}
 Click $main 1022
 $warningLine=$null
 for($i=0;$i -lt 12;$i++){
  $warningLine=[LabUI]::Windows($client.Id)|Where-Object {$_ -match '\|#32770\|.*\|Warning!$'}|Select-Object -First 1
  if($warningLine){break}
  Start-Sleep -Milliseconds 250
 }
 if($warningLine){
  $warning=[IntPtr][long]($warningLine.Split('|')[0])
  $warningText=[LabUI]::Text([LabUI]::GetDlgItem($warning,65535))
  throw "PI Client still displayed connection warning: $warningText"
 }
 'SHUTDOWN_NOTIFICATION_WARNING_ABSENT'
 if($Registered){
  $candidates=@(Get-WmiObject Win32_Process|Where-Object {$_.ExecutablePath -eq $ServerExecutable -and $_.SessionId -eq $sessionId -and $_.CommandLine -match ' -Embedding(\s|$)'})
  if($candidates.Count -ne 1){throw 'Expected one newly activated registered lab server.'}
  $server=Get-Process -Id $candidates[0].ProcessId
  'TEST_REGISTERED_SERVER pid='+$server.Id+' sha256='+$hash
 }
 Click $main 1019
 $group=Dialog 'Add Group'
 if([LabUI]::Text([LabUI]::GetDlgItem($group,1032)) -ne 'v2.05a' -or
    [LabUI]::SendMessage([LabUI]::GetDlgItem($group,1014),0xf0,[IntPtr]::Zero,[IntPtr]::Zero).ToInt32() -ne 1){throw 'Group defaults must be active DA v2.05a.'}
 Click $group 1
 $groups=[LabUI]::GetDlgItem($main,1003)
 if([LabUI]::SendMessage($groups,0x1032,[IntPtr]::Zero,[IntPtr]::Zero).ToInt32() -ne 1){throw 'No group selected.'}
 Click $main 1016
 $add=Dialog 'Add Item'
 [LabUI]::SendMessage([LabUI]::GetDlgItem($add,1032),0xf1,[IntPtr]1,[IntPtr]::Zero)|Out-Null
 Click $add 1032
 Click $add 1019
 $browse=[LabUI]::GetDlgItem($add,1002)
 $count=[LabUI]::SendMessage($browse,0x1004,[IntPtr]::Zero,[IntPtr]::Zero).ToInt32()
 $ids=@();for($i=0;$i -lt $count;$i++){$ids+=[LabUI]::Cell($browse,$i,1)}
 if($count -ne $expectedCount -or @($ExpectedItemIds|Where-Object {!($ids -contains $_)}).Count){throw 'Unexpected browse items.'}
 Click $add 1037
 Click $add 1033
 Click $add 1
 if([LabUI]::Text([LabUI]::GetDlgItem($main,1029)) -ne 'Cache'){throw 'Expected unchanged Cache default.'}
 VerifyRefresh 'online' 'Good'
 if(!$Registered){
  New-Item -ItemType File -Path $flag|Out-Null;$createdFlag=$true
  Start-Sleep -Seconds 2
  VerifyRefresh 'offline' 'Bad'
  Start-Sleep -Seconds 2
  VerifyRefresh 'offline-repeat' 'Bad'
  if($phaseTimestamp['offline'] -ne $phaseTimestamp['offline-repeat']){throw 'Offline source timestamp changed without a new sample.'}
  Remove-Item -LiteralPath $flag;$createdFlag=$false
  Start-Sleep -Seconds 2
  VerifyRefresh 'recovered' 'Good'
  if($phaseTimestamp['recovered'] -le $phaseTimestamp['offline']){throw 'Recovery did not carry a new source timestamp.'}
 }
 $before=@(Get-Content $log).Count
 Click $main 1028
 $new=@(Get-Content $log|Select-Object -Skip $before|Where-Object {$_ -match ('pid='+$server.Id+' ')})
 if(@($new|Where-Object {$_ -match '\[COM_SYNCIO\].*RETURN hresult=S_OK'}).Count -ne 1){throw 'Sync regression failed.'}
 $lifetime=@(Get-Content $log|Where-Object {$_ -match ('pid='+$server.Id+' ')})
 if(@($lifetime|Where-Object {$_ -match '\[SHUTDOWN_CALLBACK_CONNECT\]'}).Count -lt 1){throw 'PI Client did not register IOPCShutdown.'}
 'SHUTDOWN_CALLBACK_REGISTERED_PASS'
 if($Registered){'REGISTERED_REFRESH_AND_SYNC_PASS'}else{'REFRESH_GOOD_BAD_STABLE_TIMESTAMP_GOOD_AND_SYNC_PASS'}
} finally {
 if($createdFlag -and (Test-Path $flag)){Remove-Item -LiteralPath $flag}
 if($client){$q=Get-WmiObject Win32_Process -Filter ("ProcessId="+$client.Id);if($q -and $q.ExecutablePath -eq $ClientPath -and $q.SessionId -eq $client.SessionId){'STOP_TEST_CLIENT='+$q.ProcessId;Stop-Process -Id $q.ProcessId -Force}}
 if($server){$q=Get-WmiObject Win32_Process -Filter ("ProcessId="+$server.Id);if($q -and $q.ExecutablePath -eq $ServerExecutable -and $q.SessionId -eq $sessionId -and ($q.CommandLine -match ' --volatile-server(\s|$)' -or ($Registered -and $q.CommandLine -match ' -Embedding(\s|$)'))){'STOP_TEST_SERVER='+$q.ProcessId;Stop-Process -Id $q.ProcessId -Force}}
}
