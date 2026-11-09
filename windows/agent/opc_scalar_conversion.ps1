# SPDX-License-Identifier: MIT
# Scalar-only conversions. Compatible with Windows PowerShell 2/.NET 2.
function BridgeObservedVariantType($value){
 if($null -eq $value){return $null}
 switch($value.GetType().FullName){
  'System.Boolean'{return 11};'System.Double'{return 5};'System.Single'{return 4}
  'System.String'{return 8};'System.SByte'{return 16};'System.Byte'{return 17}
  'System.Int16'{return 2};'System.UInt16'{return 18};'System.Int32'{return 3}
  'System.UInt32'{return 19};'System.Int64'{return 20};'System.UInt64'{return 21}
 }
 return $null
}
function BridgePublishedVariantType([string]$type){
 switch($type){'Double'{return 5};'Integer'{return 3};'String'{return 8};'Boolean'{return 11}}
 throw 'unsupported_output_type'
}
function ConvertBridgeScalar($value,[string]$type){
 $vt=BridgeObservedVariantType $value
 if($null -eq $vt){throw 'unsupported_source_scalar'}
 $culture=[Globalization.CultureInfo]::InvariantCulture
 $style=[Globalization.NumberStyles]::Float
 if(($vt -eq 4 -or $vt -eq 5) -and ([double]::IsNaN([double]$value) -or [double]::IsInfinity([double]$value))){throw 'non_finite_source'}
 if($type -eq 'String'){
  $text=if($vt -eq 8){$value}elseif($vt -eq 11){if($value){'true'}else{'false'}}else{[Convert]::ToString($value,$culture)}
  if($text.Length -gt 4096){throw 'string_too_long'}
  # Refuse isolated surrogates; paired Unicode and embedded NUL are supported.
  $encoding=New-Object Text.UTF8Encoding($false,$true)
  $null=$encoding.GetByteCount($text)
  return ,([string]$text)
 }
 if($type -eq 'Boolean'){
  if($vt -eq 11){return ,([bool]$value)}
  if($vt -eq 8){
   switch($value.Trim().ToLowerInvariant()){'true'{return ,$true};'1'{return ,$true};'false'{return ,$false};'0'{return ,$false}}
   throw 'invalid_boolean_text'
  }
  if($value -eq 0){return ,$false};if($value -eq 1){return ,$true}
  throw 'boolean_requires_zero_or_one'
 }
 if($type -eq 'Integer'){
  $number=[decimal]0
  if($vt -eq 8){
   # Exact integer text avoids Decimal/Double rounding hiding tiny fractions.
   $text=$value.Trim();$integer=[int]0
   if($text -notmatch '^[+-]?[0-9]+$' -or ![int]::TryParse($text,[Globalization.NumberStyles]::AllowLeadingSign,$culture,[ref]$integer)){throw 'invalid_integer_text'}
   $number=[decimal]$integer
  }elseif($vt -eq 11){$number=if($value){[decimal]1}else{[decimal]0}}
  else{$number=[decimal]$value}
  if($number -lt -2147483648 -or $number -gt 2147483647 -or [decimal]::Truncate($number) -ne $number){throw 'integer_range_or_fraction'}
  # Check floating input before decimal rounding can conceal a fraction.
  if(($vt -eq 4 -or $vt -eq 5) -and [Math]::Truncate([double]$value) -ne [double]$value){throw 'integer_fraction'}
  return ,([int]$number)
 }
 if($type -eq 'Double'){
  $number=[double]0
  if($vt -eq 8){
   if(![double]::TryParse($value.Trim(),$style,$culture,[ref]$number)){throw 'invalid_real_text'}
  }elseif($vt -eq 11){$number=if($value){[double]1}else{[double]0}}
  else{
   if(($vt -eq 20 -or $vt -eq 21) -and ([decimal]$value -lt -9007199254740992 -or [decimal]$value -gt 9007199254740992)){throw 'integer_precision_loss'}
   $number=[double]$value
  }
  if([double]::IsNaN($number) -or [double]::IsInfinity($number)){throw 'non_finite_output'}
  return ,([double]$number)
 }
 throw 'unsupported_output_type'
}
