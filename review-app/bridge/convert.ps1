param([string]$SourcePath,[string]$PdfOut,[string]$InfoOut,[switch]$ExtractOnly,[switch]$SkipBodyOpenings)
$ErrorActionPreference='Stop'
. "$PSScriptRoot/printer.ps1"
# Compile the verified extractor without executing its example entry point.
$code=Get-Content -Raw "$PSScriptRoot/extractor.ps1"
$code=$code.Replace('[SwInventory]::Run($args[0])','')
Invoke-Expression $code
[SwInventory]::SkipBodyOpenings = $SkipBodyOpenings.IsPresent
Add-Type -ReferencedAssemblies 'D:/SOLIDWORKS Corp/SOLIDWORKS/SolidWorks.Interop.sldworks.dll' -TypeDefinition @"
using System;
using SolidWorks.Interop.sldworks;
public class SheetReader {
 public static string[] Names(string path) { var s=(ISldWorks)Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application")); int e=0,w=0;var m=s.OpenDoc6(path,3,3,"",ref e,ref w);if(m==null)throw new Exception("Open failed "+e);if(!String.Equals(System.IO.Path.GetFullPath(m.GetPathName()),System.IO.Path.GetFullPath(path),StringComparison.OrdinalIgnoreCase))throw new Exception("A different drawing with the same name is already open; close that document before uploading.");return (string[])((IDrawingDoc)m).GetSheetNames(); }
 public static void Activate(string path,string name) { var s=(ISldWorks)Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application"));int e=0;var m=(IModelDoc2)s.ActivateDoc3(System.IO.Path.GetFileName(path),false,0,ref e);if(m==null){int w=0;m=s.OpenDoc6(path,3,3,"",ref e,ref w);}if(!((IDrawingDoc)m).ActivateSheet(name))throw new Exception("Cannot activate sheet "+name); }
}
"@
[Console]::WriteLine("Opening drawing and listing sheets")
$pages=@()
foreach($name in [SheetReader]::Names($SourcePath)) {
 [Console]::WriteLine("Activating sheet: " + $name)
 [SheetReader]::Activate($SourcePath,$name)
 [Console]::WriteLine("Extracting sheet geometry: " + $name)
 $pages+=@{name=$name;raw=[SwInventory]::Run($SourcePath)}
}
[Console]::WriteLine("Writing extraction JSON")
ConvertTo-Json -InputObject @($pages) -Depth 8 | Set-Content -Encoding utf8 $InfoOut
if (!$ExtractOnly) {
 [Console]::WriteLine("Printing PDF")
 [CompletePdfPrinter]::Run($SourcePath,$PdfOut)
}

