# SPDX-License-Identifier: MIT
# Generic IDispatch invocation for CLR 2/PowerShell 2. No OPC interfaces/IDLs.
# ABI: Windows IDispatch, VARIANT and DISPPARAMS; delegates exist only in memory.
function InitializeBridgeDispatch {
 if($script:bridgeDispatchInitialized){return}
 $an=New-Object Reflection.AssemblyName ('BridgeDispatch_'+[Guid]::NewGuid().ToString('N'))
 $assembly=[AppDomain]::CurrentDomain.DefineDynamicAssembly($an,[Reflection.Emit.AssemblyBuilderAccess]::Run)
 $script:bridgeDispatchModule=$assembly.DefineDynamicModule('Interop')
 $script:bridgeNamesDelegate=NewBridgeDelegate 'Names' ([int]) ([Type[]]@([IntPtr],[Guid].MakeByRefType(),[IntPtr],[UInt32],[UInt32],[int].MakeByRefType()))
 $script:bridgeInvokeDelegate=NewBridgeDelegate 'Invoke' ([int]) ([Type[]]@([IntPtr],[int],[Guid].MakeByRefType(),[UInt32],[UInt16],[IntPtr],[IntPtr],[IntPtr],[UInt32].MakeByRefType()))
 $native=$script:bridgeDispatchModule.DefineType('Native',[Reflection.TypeAttributes]'Public,Sealed,Abstract')
 $method=$native.DefinePInvokeMethod('VariantClear','oleaut32.dll',[Reflection.MethodAttributes]'Public,Static,PinvokeImpl',[Reflection.CallingConventions]::Standard,[int],[Type[]]@([IntPtr]),[Runtime.InteropServices.CallingConvention]::Winapi,[Runtime.InteropServices.CharSet]::Auto)
 $method.SetImplementationFlags([Reflection.MethodImplAttributes]::PreserveSig)
 $script:bridgeVariantClear=$native.CreateType().GetMethod('VariantClear')
 $script:bridgeDispatchInitialized=$true
}
function NewBridgeDelegate($name,[Type]$result,[Type[]]$parameters){
 $type=$script:bridgeDispatchModule.DefineType($name,[Reflection.TypeAttributes]'Public,Sealed',[MulticastDelegate])
 $ctor=$type.DefineConstructor([Reflection.MethodAttributes]'Public,HideBySig,RTSpecialName',[Reflection.CallingConventions]::Standard,[Type[]]@([object],[IntPtr]))
 $ctor.SetImplementationFlags([Reflection.MethodImplAttributes]'Runtime,Managed')
 $invoke=$type.DefineMethod('Invoke',[Reflection.MethodAttributes]'Public,HideBySig,NewSlot,Virtual',$result,$parameters)
 $invoke.SetImplementationFlags([Reflection.MethodImplAttributes]'Runtime,Managed')
 return $type.CreateType()
}
function NewBridgeMemory([int]$size){
 $pointer=[Runtime.InteropServices.Marshal]::AllocCoTaskMem($size)
 for($i=0;$i -lt $size;$i++){[Runtime.InteropServices.Marshal]::WriteByte($pointer,$i,0)}
 return $pointer
}
function InvokeBridgeDispatch($target,[string]$name,[object[]]$values,[bool[]]$references,[UInt16]$flags=1){
 InitializeBridgeDispatch
 if([IntPtr]::Size -ne 4){throw 'This laboratory dispatch bridge is validated for x86 only'}
 if($values.Count -ne $references.Count){throw 'Dispatch argument mask mismatch'}
 $dispatch=[IntPtr]::Zero;$names=[IntPtr]::Zero;$text=[IntPtr]::Zero
 $variants=[IntPtr]::Zero;$result=[IntPtr]::Zero;$params=[IntPtr]::Zero;$exception=[IntPtr]::Zero
 $outputs=@{};$count=$values.Count
 try{
  $dispatch=[Runtime.InteropServices.Marshal]::GetIDispatchForObject($target.PSObject.BaseObject)
  $vtable=[Runtime.InteropServices.Marshal]::ReadIntPtr($dispatch)
  $namesFn=[Runtime.InteropServices.Marshal]::GetDelegateForFunctionPointer([Runtime.InteropServices.Marshal]::ReadIntPtr($vtable,5*[IntPtr]::Size),$script:bridgeNamesDelegate)
  $text=[Runtime.InteropServices.Marshal]::StringToCoTaskMemUni($name)
  $names=NewBridgeMemory ([IntPtr]::Size);[Runtime.InteropServices.Marshal]::WriteIntPtr($names,$text)
  $nameArgs=[object[]]@($dispatch,[Guid]::Empty,$names,[UInt32]1,[UInt32]0,[int]0)
  for($j=0;$j -lt $nameArgs.Length;$j++){$nameArgs[$j]=$nameArgs[$j].PSObject.BaseObject}
  $hr=[int]$namesFn.DynamicInvoke($nameArgs)
  if($hr -lt 0){throw (New-Object Runtime.InteropServices.COMException -ArgumentList @('IDispatch.GetIDsOfNames failed',$hr))}
  $dispid=[int]$nameArgs[5]
  # VARIANT is 16 bytes on x86. IDispatch stores positional arguments reversed.
  $variants=NewBridgeMemory ([Math]::Max(1,$count)*16)
  for($i=0;$i -lt $count;$i++){
   $arg=New-Object IntPtr ($variants.ToInt64()+($count-1-$i)*16)
   if($references[$i]){
    $out=NewBridgeMemory 16;$outputs[$i]=$out
    $base=$null;if($null -ne $values[$i]){$base=$values[$i].PSObject.BaseObject}
    [Runtime.InteropServices.Marshal]::GetNativeVariantForObject($base,$out)
    [Runtime.InteropServices.Marshal]::WriteInt16($arg,0,[Int16]16396) # VT_BYREF|VT_VARIANT
    [Runtime.InteropServices.Marshal]::WriteIntPtr($arg,8,$out)
   }else{$base=$null;if($null -ne $values[$i]){$base=$values[$i].PSObject.BaseObject};[Runtime.InteropServices.Marshal]::GetNativeVariantForObject($base,$arg)}
  }
  $dp=New-Object Runtime.InteropServices.ComTypes.DISPPARAMS
  $dp.rgvarg=$variants;$dp.cArgs=$count;$dp.cNamedArgs=0;$dp.rgdispidNamedArgs=[IntPtr]::Zero
  $params=NewBridgeMemory ([Runtime.InteropServices.Marshal]::SizeOf($dp))
  [Runtime.InteropServices.Marshal]::StructureToPtr($dp,$params,$false)
  $result=NewBridgeMemory 16
  $exception=NewBridgeMemory ([Runtime.InteropServices.Marshal]::SizeOf([type][Runtime.InteropServices.ComTypes.EXCEPINFO]))
  $invokeFn=[Runtime.InteropServices.Marshal]::GetDelegateForFunctionPointer([Runtime.InteropServices.Marshal]::ReadIntPtr($vtable,6*[IntPtr]::Size),$script:bridgeInvokeDelegate)
  $invokeArgs=[object[]]@($dispatch,$dispid,[Guid]::Empty,[UInt32]0,$flags,$params,$result,$exception,[UInt32]0)
  for($j=0;$j -lt $invokeArgs.Length;$j++){$invokeArgs[$j]=$invokeArgs[$j].PSObject.BaseObject}
  $hr=[int]$invokeFn.DynamicInvoke($invokeArgs)
  if($hr -lt 0){
   # Preserve the component's scode for DISP_E_EXCEPTION; never expose BSTR text.
   $info=[Runtime.InteropServices.Marshal]::PtrToStructure($exception,[type][Runtime.InteropServices.ComTypes.EXCEPINFO])
   if($hr -eq -2147352567 -and $info.scode -lt 0){$hr=$info.scode}
   throw (New-Object Runtime.InteropServices.COMException -ArgumentList @('IDispatch.Invoke failed',$hr))
  }
  $returned=New-Object 'Object[]' $count
  for($i=0;$i -lt $count;$i++){
   if($references[$i]){$returned[$i]=[Runtime.InteropServices.Marshal]::GetObjectForNativeVariant($outputs[$i])}else{$returned[$i]=$values[$i]}
  }
  return @{arguments=$returned;value=[Runtime.InteropServices.Marshal]::GetObjectForNativeVariant($result);hresult=$hr;dispid=$dispid}
 }finally{
  foreach($out in $outputs.Values){$script:bridgeVariantClear.Invoke($null,[object[]]@([IntPtr]$out))|Out-Null;[Runtime.InteropServices.Marshal]::FreeCoTaskMem($out)}
  if($variants -ne [IntPtr]::Zero){
   for($i=0;$i -lt $count;$i++){$arg=New-Object IntPtr ($variants.ToInt64()+$i*16);$script:bridgeVariantClear.Invoke($null,[object[]]@([IntPtr]$arg))|Out-Null}
   [Runtime.InteropServices.Marshal]::FreeCoTaskMem($variants)
  }
  if($result -ne [IntPtr]::Zero){$script:bridgeVariantClear.Invoke($null,[object[]]@([IntPtr]$result))|Out-Null;[Runtime.InteropServices.Marshal]::FreeCoTaskMem($result)}
  if($params -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::FreeCoTaskMem($params)}
  if($exception -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::DestroyStructure($exception,[type][Runtime.InteropServices.ComTypes.EXCEPINFO]);[Runtime.InteropServices.Marshal]::FreeCoTaskMem($exception)}
  if($names -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::FreeCoTaskMem($names)}
  if($text -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::FreeCoTaskMem($text)}
  if($dispatch -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::Release($dispatch)|Out-Null}
 }
}

# Read installed COM type information only. No OPC declarations are generated.
function InvokeBridgeTypeInfo($info,[string]$name,[object[]]$arguments){
 for($j=0;$j -lt $arguments.Length;$j++){if($null -ne $arguments[$j]){$arguments[$j]=$arguments[$j].PSObject.BaseObject}}
 [Runtime.InteropServices.ComTypes.ITypeInfo].GetMethod($name).Invoke($info.PSObject.BaseObject,$arguments)|Out-Null
 return ,$arguments
}
function DescribeBridgeType($description,[int]$depth=0){
 $result=@{vt=[int]$description.vt}
 if($depth -lt 4 -and (@(26,27) -contains [int]$description.vt)){
  $nested=[Runtime.InteropServices.Marshal]::PtrToStructure($description.lpValue,[type][Runtime.InteropServices.ComTypes.TYPEDESC])
  $result['element']=DescribeBridgeType $nested ($depth+1)
 }
 return $result
}
function GetBridgeDispatchDescription($target,[string]$member){
 InitializeBridgeDispatch
 $delegate=NewBridgeDelegate ('TypeInfo_'+[Guid]::NewGuid().ToString('N')) ([int]) ([Type[]]@([IntPtr],[UInt32],[UInt32],[IntPtr].MakeByRefType()))
 $dispatch=[IntPtr]::Zero;$pointer=[IntPtr]::Zero;$info=$null;$attrPointer=[IntPtr]::Zero
 try{
  $dispatch=[Runtime.InteropServices.Marshal]::GetIDispatchForObject($target.PSObject.BaseObject)
  $vtable=[Runtime.InteropServices.Marshal]::ReadIntPtr($dispatch)
  $fn=[Runtime.InteropServices.Marshal]::GetDelegateForFunctionPointer([Runtime.InteropServices.Marshal]::ReadIntPtr($vtable,4*[IntPtr]::Size),$delegate)
  $call=[object[]]@($dispatch,[UInt32]0,[UInt32]0,[IntPtr]::Zero)
  for($j=0;$j -lt $call.Length;$j++){$call[$j]=$call[$j].PSObject.BaseObject}
  $hr=[int]$fn.DynamicInvoke($call)
  if($hr -lt 0){throw (New-Object Runtime.InteropServices.COMException -ArgumentList @('GetTypeInfo failed',$hr))}
  $pointer=[IntPtr]$call[3];$info=[Runtime.InteropServices.Marshal]::GetObjectForIUnknown($pointer)
  $attrs=InvokeBridgeTypeInfo $info 'GetTypeAttr' ([object[]]@([IntPtr]::Zero));$attrPointer=[IntPtr]$attrs[0]
  $attr=[Runtime.InteropServices.Marshal]::PtrToStructure($attrPointer,[type][Runtime.InteropServices.ComTypes.TYPEATTR])
  for($i=0;$i -lt $attr.cFuncs;$i++){
   $functionPointer=[IntPtr]::Zero
   try{
    $f=InvokeBridgeTypeInfo $info 'GetFuncDesc' ([object[]]@([int]$i,[IntPtr]::Zero));$functionPointer=[IntPtr]$f[1]
    $function=[Runtime.InteropServices.Marshal]::PtrToStructure($functionPointer,[type][Runtime.InteropServices.ComTypes.FUNCDESC])
    $doc=InvokeBridgeTypeInfo $info 'GetDocumentation' ([object[]]@([int]$function.memid,$null,$null,[int]0,$null))
    if($doc[1] -ne $member){continue}
    $parameters=@();$size=[Runtime.InteropServices.Marshal]::SizeOf([type][Runtime.InteropServices.ComTypes.ELEMDESC])
    for($p=0;$p -lt $function.cParams;$p++){
     $address=New-Object IntPtr ($function.lprgelemdescParam.ToInt64()+$p*$size)
     $element=[Runtime.InteropServices.Marshal]::PtrToStructure($address,[type][Runtime.InteropServices.ComTypes.ELEMDESC])
     $parameters+=@{index=$p;flags=[int]$element.desc.paramdesc.wParamFlags;type=(DescribeBridgeType $element.tdesc)}
    }
    return @{member=$member;dispid=[int]$function.memid;parameter_count=[int]$function.cParams;optional_count=[int]$function.cParamsOpt;parameters=$parameters}
   }finally{if($functionPointer -ne [IntPtr]::Zero){InvokeBridgeTypeInfo $info 'ReleaseFuncDesc' ([object[]]@($functionPointer))|Out-Null}}
  }
  return @{member=$member;not_found=$true}
 }finally{
  if($attrPointer -ne [IntPtr]::Zero){InvokeBridgeTypeInfo $info 'ReleaseTypeAttr' ([object[]]@($attrPointer))|Out-Null}
  if($info){[Runtime.InteropServices.Marshal]::ReleaseComObject($info)|Out-Null}
  if($pointer -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::Release($pointer)|Out-Null}
  if($dispatch -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::Release($dispatch)|Out-Null}
 }
}
