Add-Type -Path 'D:/SOLIDWORKS Corp/SOLIDWORKS/SolidWorks.Interop.sldworks.dll'
Add-Type -ReferencedAssemblies 'D:/SOLIDWORKS Corp/SOLIDWORKS/SolidWorks.Interop.sldworks.dll' -TypeDefinition @"
using System;using SolidWorks.Interop.sldworks;
public class Paths {public static string Read(){var s=(ISldWorks)Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application"));var m=(IModelDoc2)s.IActiveDoc2;return m.GetPathName();}}
"@
[Paths]::Read()
