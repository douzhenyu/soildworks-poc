param([string]$ManifestPath)
$ErrorActionPreference='Stop'
Add-Type -Path 'D:/SOLIDWORKS Corp/SOLIDWORKS/SolidWorks.Interop.sldworks.dll'
Add-Type -ReferencedAssemblies 'D:/SOLIDWORKS Corp/SOLIDWORKS/SolidWorks.Interop.sldworks.dll' -TypeDefinition @"
using System;
using SolidWorks.Interop.sldworks;
public class ReferenceRedirector {
 public static string[] Dependencies(string path) {
  var s=(ISldWorks)Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application"));
  var raw=s.GetDocumentDependencies2(path,false,false,false) as Array;
  if(raw==null)return new string[0];
  var result=new string[raw.Length];for(int i=0;i<raw.Length;i++)result[i]=Convert.ToString(raw.GetValue(i));return result;
 }
 public static bool Replace(string file,string original,string target) {
  var s=(ISldWorks)Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application"));
  return s.ReplaceReferencedDocument(file,original,target);
 }
}
"@
$entries=Get-Content -Raw -Encoding UTF8 $ManifestPath | ConvertFrom-Json
$log=@()
foreach($entry in $entries) {
 if($entry.prepared) { continue }
 $warnings=@()
 $refs=[ReferenceRedirector]::Dependencies($entry.work)
 for($i=0;$i+1 -lt $refs.Length;$i+=2) {
  $old=$refs[$i+1]
  $ext=[IO.Path]::GetExtension($old).ToLowerInvariant()
  if($ext -notin @('.sldprt','.sldasm','.slddrw')) { continue }
  $candidates=@($entries | Where-Object { [IO.Path]::GetFileName($_.source) -ieq [IO.Path]::GetFileName($old) })
  if($candidates.Count -eq 0) {
   $warnings+="压缩包未包含引用文件：$old；仅能依据缓存图形初审。"
   $log+="WARNING $($entry.work) missing: $old"
   continue
  }
  if($candidates.Count -gt 1) {
   throw "配套模型存在同名歧义：$old；请消除压缩包中的同名模型歧义。"
  }
  if(-not [ReferenceRedirector]::Replace($entry.work,$old,$candidates[0].work)) {
   throw "无法重定向工作副本的模型引用：$old"
  }
  $log+="$($entry.work) -> $($candidates[0].work)"
 }
 $entry | Add-Member -NotePropertyName warnings -NotePropertyValue @($warnings) -Force
}
$entries | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $ManifestPath
$log
