param([string]$SourceRoot='D:/shukong/负向样本-外发',[string]$OutputRoot='D:/shukong/output/pdf/完整图纸')
Add-Type -Path 'D:/SOLIDWORKS Corp/SOLIDWORKS/SolidWorks.Interop.sldworks.dll'
Add-Type -ReferencedAssemblies 'D:/SOLIDWORKS Corp/SOLIDWORKS/SolidWorks.Interop.sldworks.dll' -TypeDefinition @"
using System;
using SolidWorks.Interop.sldworks;
public class CompletePdfPrinter {
 public static string Run(string path,string output) {
 ISldWorks sw=(ISldWorks)Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application"));
 int e=0,w=0; IModelDoc2 m=sw.OpenDoc6(path,3,3,"",ref e,ref w);
 if(m==null)throw new Exception("Open failed: "+e);
 IDrawingDoc d=(IDrawingDoc)m;
 string[] sheets=(string[])d.GetSheetNames();
 
 int ae=0; sw.ActivateDoc3(m.GetTitle(),false,0,ref ae);
 ISheet sh=(ISheet)d.GetCurrentSheet(); double width=0,height=0;sh.GetSize(ref width,ref height);
 string originalSheet=sh.GetName();
 foreach(string name in sheets) {
   d.ActivateSheet(name); ISheet check=(ISheet)d.GetCurrentSheet(); double cw=0,ch=0;check.GetSize(ref cw,ref ch);
   if(Math.Abs(cw-width)>0.001 || Math.Abs(ch-height)>0.001) { d.ActivateSheet(originalSheet); throw new Exception("Mixed sheet sizes require per-sheet print setup; conversion stopped to prevent cropping."); }
 }
 d.ActivateSheet(originalSheet);
 double longer=Math.Max(width,height),shorter=Math.Min(width,height);
 bool a3=Math.Abs(longer-.420)<.001 && Math.Abs(shorter-.297)<.001;
 bool a4=Math.Abs(longer-.297)<.001 && Math.Abs(shorter-.210)<.001;
 if(!a3 && !a4)throw new Exception("Only A3/A4 printing is validated; conversion stopped to prevent cropping.");
 m.ClearSelection2(true);
 IPageSetup setup=(IPageSetup)m.PageSetup;
int oldUse=m.Extension.UsePageSetup,oldPaper=setup.PrinterPaperSize,oldOrientation=setup.Orientation;
bool oldFit=setup.ScaleToFit; double oldScale=setup.Scale2;
try {
m.Extension.UsePageSetup=2;
setup.PrinterPaperSize=a3?8:9;
setup.Orientation=width>=height?2:1;setup.ScaleToFit=false;setup.Scale2=100;
m.Extension.PrintOut2(new int[]{1,sheets.Length},1,false,"Microsoft Print to PDF",output);
} finally {setup.PrinterPaperSize=oldPaper;setup.Orientation=oldOrientation;setup.ScaleToFit=oldFit;setup.Scale2=oldScale;m.Extension.UsePageSetup=oldUse;}
 return System.IO.Path.GetFileName(path)+" | sheets="+sheets.Length+" | size_m="+width+","+height+" | openError="+e+" | warning="+w+" | output="+output;
 }
}
"@

