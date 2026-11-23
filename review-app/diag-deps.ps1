Add-Type -Path 'D:/SOLIDWORKS Corp/SOLIDWORKS/SolidWorks.Interop.sldworks.dll'
Add-Type -ReferencedAssemblies 'D:/SOLIDWORKS Corp/SOLIDWORKS/SolidWorks.Interop.sldworks.dll' -TypeDefinition @"
using System;using SolidWorks.Interop.sldworks;
public class DepsDiagnostic {public static string Run(string p){var s=(ISldWorks)Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application"));var x=s.GetDocumentDependencies2(p,false,false,false); if(x==null)return "NULL "+p;var a=x as Array;return x.GetType().FullName+" length="+a.Length+" "+String.Join(" | ", Array.ConvertAll((object[])a,v=>v.ToString()));}}
"@
$m=Get-Content -Raw -Encoding utf8 'D:/shukong/review-app/data/0d0ca1e2120746fb91cfd5a01fe408c7/cad-manifest.json' | ConvertFrom-Json
$m | Where-Object relative -like '*39.SLDDRW' | ForEach-Object {[DepsDiagnostic]::Run($_.source);[DepsDiagnostic]::Run($_.work)}
