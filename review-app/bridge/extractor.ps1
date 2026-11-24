Add-Type -Path 'D:/SOLIDWORKS Corp/SOLIDWORKS/SolidWorks.Interop.sldworks.dll'
Add-Type -ReferencedAssemblies 'D:/SOLIDWORKS Corp/SOLIDWORKS/SolidWorks.Interop.sldworks.dll' -TypeDefinition @"
using System;
using System.Text;
using System.Globalization;
using System.Threading;
using System.Collections.Generic;
using SolidWorks.Interop.sldworks;
public class SwInventory {
 public static bool SkipBodyOpenings;
 sealed class Opening {
   public string EdgeId, WallId;
   public double[] Circle;
 }
 static Opening MatchOpening(IModelDoc2 model, IEdge edge, double[] circle, List<Opening> openings, out string method) {
   method = "";
   if (openings == null || circle == null || circle.Length != 7) return null;
   var matches = new List<Opening>();
   string edgeId = PersistentId(model, edge);
   byte[] reference = edgeId.Length == 0 ? null : Convert.FromBase64String(edgeId);
   foreach (Opening opening in openings) {
     double[] candidate = opening.Circle;
     double axisDot = circle[3]*candidate[3]+circle[4]*candidate[4]+circle[5]*candidate[5];
     if (Math.Abs(Math.Abs(axisDot)-1) > .000001 || Math.Abs(circle[6]-candidate[6]) > .0000001) continue;
     if (reference != null && model.Extension.IsSamePersistentID(reference, Convert.FromBase64String(opening.EdgeId)) == 1) {
       method = "persistent";
       return opening;
     }
     if (Math.Abs(circle[0]-candidate[0]) <= .0000001 &&
         Math.Abs(circle[1]-candidate[1]) <= .0000001 &&
         Math.Abs(circle[2]-candidate[2]) <= .0000001) matches.Add(opening);
   }
   if (matches.Count != 1) return null;
   method = "unique-circle";
   return matches[0];
 }
 static string PersistentId(IModelDoc2 owner, object target) {
   try {
     byte[] bytes = owner.Extension.GetPersistReference3(target) as byte[];
     return bytes == null || bytes.Length == 0 ? "" : Convert.ToBase64String(bytes);
   } catch { return ""; }
 }
 static double Distance(double[] a, double[] b) {
   double x=a[0]-b[0], y=a[1]-b[1], z=a[2]-b[2];
   return Math.Sqrt(x*x+y*y+z*z);
 }
 static void ReadVisibleEdges(StringBuilder output, IView view) {
   try {
     Array components = view.GetVisibleComponents() as Array;
     int count = 0, circles = 0;
     if (components != null) foreach (IComponent2 component in components) {
       Array edges = view.GetVisibleEntities2((Component2)component,1) as Array;
       if (edges == null) continue;
       foreach (IEdge edge in edges) {
         count++;
         ICurve curve = edge.GetCurve() as ICurve;
         if (curve == null) continue;
         if (curve.IsCircle()) {
           circles++;
           output.AppendLine("VisibleCircle: " + Fmt(curve.CircleParams));
           ICurveParamData trim = edge.GetCurveParams3();
           if (trim != null && Math.Abs(Math.Abs(trim.UMaxValue-trim.UMinValue)-2*Math.PI) < .000001)
             output.AppendLine("VisibleFullCircle: " + Fmt(curve.CircleParams));
         } else if (curve.IsLine()) {
           ICurveParamData trim = edge.GetCurveParams3();
           if (trim != null) output.AppendLine("VisibleStraightEdge: " + Fmt(trim.StartPoint) + Fmt(trim.EndPoint));
         }
       }
     }
     output.AppendLine("VisibleEdges: " + count + " CircleEdges: " + circles +
                       " Components: " + (components == null ? 0 : components.Length));
   } catch(Exception ex) { output.AppendLine("INVENTORY_ERROR: " + ex.Message); }
 }
 static void ReadStraightSlot(StringBuilder output, IModelDoc2 model, Array edges) {
   if (edges.Length != 4) return;
   var circles = new List<double[]>();
   var straightLengths = new List<double>();
   var straightEdges = new List<string>();
   var wallIds = new List<string>();
   var edgeIds = new List<string>();
   foreach (IEdge edge in edges) {
     ICurve curve = edge.GetCurve() as ICurve;
     ICurveParamData trim = edge.GetCurveParams3();
     if (curve == null || trim == null) return;
     if (curve.IsCircle() && Math.Abs(Math.Abs(trim.UMaxValue-trim.UMinValue)-Math.PI) < .00001) {
       double[] circle = curve.CircleParams as double[];
       if (circle == null || circle.Length != 7) return;
       circles.Add(circle);
       var matchingWalls = new List<IFace2>();
       Array adjacent = edge.GetTwoAdjacentFaces2() as Array;
       if (adjacent == null) return;
       foreach (IFace2 wall in adjacent) {
         ISurface cylinder = wall.GetSurface() as ISurface;
         if (cylinder != null && cylinder.IsCylinder() && wall.FaceInSurfaceSense())
           matchingWalls.Add(wall);
       }
       if (matchingWalls.Count != 1) return;
       string wallId = PersistentId(model, matchingWalls[0]);
       if (wallId.Length == 0) return;
       wallIds.Add(wallId);
     } else if (curve.IsLine()) {
       double[] start=trim.StartPoint as double[], end=trim.EndPoint as double[];
       if (start == null || end == null || start.Length != 3 || end.Length != 3) return;
       straightLengths.Add(Distance(start,end));
       straightEdges.Add(Fmt(start) + Fmt(end));
     } else return;
     edgeIds.Add(PersistentId(model,edge));
   }
   if (circles.Count != 2 || straightLengths.Count != 2 || wallIds[0] == wallIds[1]) return;
   double[] a=circles[0], b=circles[1];
   double radius=a[6], span=Distance(a,b);
   double dot=a[3]*b[3]+a[4]*b[4]+a[5]*b[5];
   if (radius <= 0 || span <= .0000001 || Math.Abs(radius-b[6]) > .0000001 ||
       Math.Abs(Math.Abs(dot)-1) > .000001 ||
       Math.Abs(straightLengths[0]-span) > .0000001 ||
       Math.Abs(straightLengths[1]-span) > .0000001) return;
   wallIds.Sort(StringComparer.Ordinal);
   output.AppendLine("ModelStraightSlotOpening: width=" + 2*radius + " | length=" + (span+2*radius) +
                     " | endA=" + Fmt(a) + " | endB=" + Fmt(b) +
                     " | edges=" + String.Join(",",edgeIds) +
                     " | walls=" + String.Join(",",wallIds) +
                     " | lines=" + String.Join(";",straightEdges));
 }
 static void ReadCutoutOpenings(StringBuilder output, IModelDoc2 model, List<Opening> openings) {
   IPartDoc part = model as IPartDoc;
   if (part == null) return;
   Array bodies = part.GetBodies2(0, false) as Array; // swSolidBody, including hidden bodies
   if (bodies == null) return;
   bool complete = true;
   foreach (IBody2 body in bodies) {
     string bodyId = PersistentId(model,body);
     Array faces = body.GetFaces() as Array;
     if (faces == null) { complete = false; continue; }
     foreach (IFace2 face in faces) {
       ISurface surface = face.GetSurface() as ISurface;
       if (surface == null) { complete = false; continue; }
       if (surface.IsCylinder() && face.FaceInSurfaceSense()) {
         double[] parameters = surface.CylinderParams as double[];
         if (parameters == null || parameters.Length != 7) complete = false;
         else {
           output.AppendLine("ModelInwardCylinder: " + Fmt(parameters));
           ReadBoreWall(output,model,face,bodyId,parameters);
         }
       }
       if (!surface.IsPlane()) continue;
       Array loops = face.GetLoops() as Array;
       if (loops == null) continue;
       foreach (ILoop2 loop in loops) {
         if (loop.IsOuter()) continue;
         Array edges = loop.GetEdges() as Array;
         if (edges == null) continue;
         if (edges.Length != 1) {
           ReadStraightSlot(output, model, edges);
           continue;
         }
         IEdge edge = edges.GetValue(0) as IEdge;
         if (edge == null) continue;
         ICurve curve = edge.GetCurve() as ICurve;
         ICurveParamData trim = edge.GetCurveParams3();
         if (curve == null || !curve.IsCircle() || trim == null ||
             Math.Abs(Math.Abs(trim.UMaxValue - trim.UMinValue) - 2*Math.PI) > .00001) continue;
         Array adjacent = edge.GetTwoAdjacentFaces2() as Array;
         if (adjacent == null) continue;
         foreach (object item in adjacent) {
           IFace2 wall = item as IFace2;
           ISurface cylinder = wall == null ? null : wall.GetSurface() as ISurface;
           if (cylinder == null || !cylinder.IsCylinder() || !wall.FaceInSurfaceSense()) continue;
           string edgeId = PersistentId(model, edge), wallId = PersistentId(model, wall);
           if (edgeId.Length == 0 || wallId.Length == 0) continue;
           double[] circle = curve.CircleParams as double[];
           if (circle == null || circle.Length != 7) continue;
           openings.Add(new Opening { EdgeId=edgeId, WallId=wallId, Circle=circle });
           IFeature owner = wall.GetFeature() as IFeature;
           output.AppendLine("ModelCircularOpening: " + edgeId + " | " + wallId + " | " +
                             Fmt(circle) + " | " + SingleLine(owner == null ? "" : owner.Name) +
                             " | owner=" + (owner == null ? "" : PersistentId(model,owner)) +
                             " | type=" + (owner == null ? "" : SingleLine(owner.GetTypeName2())));
         }
       }
     }
   }
   output.AppendLine("ModelCircularOpeningInventoryEnd");
   if (complete) output.AppendLine("ModelCylinderInventoryEnd");
 }
 static void ReadBoreWall(StringBuilder output, IModelDoc2 model, IFace2 wall, string bodyId, double[] parameters) {
   try {
     string wallId = PersistentId(model,wall);
     if (wallId.Length == 0) return;
     IFeature owner = wall.GetFeature() as IFeature;
     output.AppendLine("ModelBoreWall: " + wallId + " | " + Fmt(parameters) +
                       " | body=" + bodyId +
                       " | owner=" + (owner == null ? "" : PersistentId(model,owner)) +
                       " | type=" + (owner == null ? "" : SingleLine(owner.GetTypeName2())));
     ReadCircularWallRims(output,wall,wallId);
   } catch (Exception ex) { output.AppendLine("BoreWallReadError: " + SingleLine(ex.Message)); }
 }
 static void ReadCircularWallRims(StringBuilder output, IFace2 wall, string wallId) {
   try {
     Array edges = wall.GetEdges() as Array;
     if (edges == null) return;
     foreach (IEdge edge in edges) {
       ICurve curve = edge.GetCurve() as ICurve;
       ICurveParamData trim = edge.GetCurveParams3();
       if (curve != null && curve.IsCircle() && trim != null &&
           Math.Abs(Math.Abs(trim.UMaxValue-trim.UMinValue)-2*Math.PI) < .00001)
         output.AppendLine("ModelCircularWallRim: " + wallId + " | " + Fmt(curve.CircleParams));
     }
   } catch (Exception ex) { output.AppendLine("CircularWallRimReadError: " + SingleLine(ex.Message)); }
 }
 static string SingleLine(string text) {
   return (text ?? "").Replace("\\", "\\\\").Replace("\r", "\\r").Replace("\n", "\\n");
 }
 static void ReadTables(StringBuilder output, IView view) {
   try {
     Array tables = view.GetTableAnnotations() as Array;
     if (tables == null) return;
     foreach (ITableAnnotation table in tables) {
       int rows = table.RowCount, cols = table.ColumnCount;
       IAnnotation annotation = table.GetAnnotation() as IAnnotation;
       output.AppendLine("DrawingTable: type=" + table.Type + " | rows=" + rows + " | cols=" + cols +
                         " | position=" + Fmt(annotation == null ? null : annotation.GetPosition()));
       for (int row = 0; row < rows; row++)
         for (int col = 0; col < cols; col++)
           output.AppendLine("DrawingTableCell: " + row + "," + col + " | " + SingleLine(table.get_DisplayedText2(row, col, true)));
     }
   } catch (Exception ex) { output.AppendLine("TableReadError: " + ex.Message); }
 }
 static void ReadSpatialModel(StringBuilder output, IView view, IMathUtility math, string openingInventory) {
   try {
     IModelDoc2 model = view.ReferencedDocument as IModelDoc2;
     if (model == null) return;
     string config = view.ReferencedConfiguration;
     output.AppendLine("SpatialRoot: " + model.GetPathName() + " | " + config + " | " +
                       (model is IAssemblyDoc ? "assembly" : "part"));
     if (model is IPartDoc) {
       ReadSpatialPart(output, model, config, "root", null, math, openingInventory);
     } else if (model is IAssemblyDoc) {
       IConfiguration configuration = model.GetConfigurationByName(config) as IConfiguration;
       IComponent2 root = configuration == null ? null : configuration.GetRootComponent3(true) as IComponent2;
       if (root == null) throw new InvalidOperationException("Referenced assembly configuration unavailable");
       ReadSpatialChildren(output, root, math);
     }
   } catch (Exception ex) { output.AppendLine("SpatialModelReadError: " + SingleLine(ex.Message)); }
 }
 static void ReadSpatialChildren(StringBuilder output, IComponent2 parent, IMathUtility math) {
   Array children = parent.GetChildren() as Array;
   if (children == null) return;
   foreach (IComponent2 child in children) {
     if (child.IsSuppressed()) continue;
     IModelDoc2 model = child.GetModelDoc2() as IModelDoc2;
     if (model == null) {
       output.AppendLine("SpatialModelReadError: Unresolved component " + SingleLine(child.Name2));
       continue;
     }
     if (model is IPartDoc) {
       IMathTransform transform = child.Transform2 as IMathTransform;
       if (transform == null) {
         output.AppendLine("SpatialModelReadError: Missing transform for " + SingleLine(child.Name2));
         continue;
       }
       ReadSpatialPart(output, model, child.ReferencedConfiguration, child.Name2, transform, math);
     } else if (model is IAssemblyDoc) {
       ReadSpatialChildren(output, child, math);
     }
   }
 }
 static double[] SpatialTransform(double[] xyz, IMathTransform transform, IMathUtility math, bool direction) {
   if (transform == null) return xyz;
   if (direction) {
     IMathVector vector = math.CreateVector(xyz) as IMathVector;
     return (double[])((IMathVector)vector.MultiplyTransform(transform)).ArrayData;
   }
   IMathPoint point = math.CreatePoint(xyz) as IMathPoint;
   return (double[])((IMathPoint)point.MultiplyTransform(transform)).ArrayData;
 }
 static void ReadSpatialPart(StringBuilder output, IModelDoc2 model, string config, string instance,
                             IMathTransform transform, IMathUtility math, string openingInventory = null) {
   output.AppendLine("SpatialPart: " + SingleLine(instance) + " | " + model.GetPathName() + " | " + config);
   if (transform != null) output.AppendLine("SpatialPartTransform: " + Fmt(transform.ArrayData));
   string original = model.ConfigurationManager.ActiveConfiguration.Name;
   bool change = !String.Equals(original, config, StringComparison.OrdinalIgnoreCase);
   try {
     if (change && !model.ShowConfiguration2(config))
       throw new InvalidOperationException("Cannot activate configuration " + config);
     Array bodies = ((IPartDoc)model).GetBodies2(0, false) as Array;
     if (bodies == null || bodies.Length == 0)
       throw new InvalidOperationException("No solid bodies");
     IMathTransform inverse = transform == null ? null : (IMathTransform)transform.Inverse();
     double[] box = { double.PositiveInfinity, double.PositiveInfinity, double.PositiveInfinity,
                      double.NegativeInfinity, double.NegativeInfinity, double.NegativeInfinity };
     foreach (IBody2 body in bodies) {
       for (int axis = 0; axis < 3; axis++) for (int sign = -1; sign <= 1; sign += 2) {
         double[] direction = new double[3];
         direction[axis] = sign;
         direction = SpatialTransform(direction, inverse, math, true);
         double x, y, z;
         if (!body.GetExtremePoint(direction[0], direction[1], direction[2], out x, out y, out z))
           throw new InvalidOperationException("Cannot resolve body extrema");
         double value = SpatialTransform(new double[] { x, y, z }, transform, math, false)[axis];
         int slot = sign < 0 ? axis : axis + 3;
         box[slot] = sign < 0 ? Math.Min(box[slot], value) : Math.Max(box[slot], value);
       }
       if (SkipBodyOpenings) continue;
       Array faces = body.GetFaces() as Array;
       if (faces == null) continue;
       foreach (IFace2 face in faces) {
         ISurface surface = face.GetSurface() as ISurface;
         if (surface != null && surface.IsCylinder() && face.FaceInSurfaceSense())
           ReadSpatialBend(output, model, face, surface);
         if (surface == null || !surface.IsPlane()) continue;
         double[] plane = surface.PlaneParams as double[];
         string id = PersistentId(model, face);
         if (plane == null || plane.Length != 6 || id.Length == 0) continue;
         double[] normal = SpatialTransform(new double[] { plane[0], plane[1], plane[2] }, transform, math, true);
         double[] point = SpatialTransform(new double[] { plane[3], plane[4], plane[5] }, transform, math, false);
         output.AppendLine("SpatialFace: " + id + " | " + Fmt(normal) + " | " + Fmt(point));
       }
     }
     output.AppendLine("SpatialBox: " + Fmt(box));
     if (!SkipBodyOpenings) {
       if (openingInventory == null) {
         var geometry = new StringBuilder();
         ReadCutoutOpenings(geometry, model, new List<Opening>());
         openingInventory = geometry.ToString();
       }
       foreach (string line in openingInventory.Split(new char[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries))
         if (line.StartsWith("ModelBoreWall: ") || line.StartsWith("ModelCircularWallRim: ") ||
             line.StartsWith("ModelCircularOpening: ") || line.StartsWith("ModelStraightSlotOpening: "))
           output.AppendLine("Spatial" + line);
     }
     for (IFeature f = model.FirstFeature() as IFeature; f != null; f = f.GetNextFeature() as IFeature) {
       string featureType = f.GetTypeName2();
       if (featureType == "MaterialFolder")
         output.AppendLine("SpatialMaterialLabel: " + SingleLine(f.Name));
       if (featureType != "HoleWzd" && featureType != "SheetMetal") continue;
       Array suppressed = f.IsSuppressed2(3, new string[] { config }) as Array;
       if (suppressed == null || suppressed.Length != 1 || Convert.ToBoolean(suppressed.GetValue(0))) continue;
       if (featureType == "SheetMetal") {
         ISheetMetalFeatureData sheet = f.GetDefinition() as ISheetMetalFeatureData;
         if (sheet != null)
           output.AppendLine("SpatialSheetMetal: " + PersistentId(model,f) + " | " + SingleLine(f.Name) +
                             " | " + Fmt(new double[] { sheet.Thickness, sheet.BendRadius, sheet.KFactor }));
         continue;
       }
       var hole = new StringBuilder();
       ReadWizardHole(hole, model, f, math);
       foreach (string line in hole.ToString().Split(new char[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries))
         output.AppendLine("Spatial" + line);
     }
   } catch (Exception ex) { output.AppendLine("SpatialModelReadError: " + SingleLine(instance) + ": " + SingleLine(ex.Message)); }
   finally {
     if (change && !model.ShowConfiguration2(original))
       output.AppendLine("SpatialModelReadError: Cannot restore " + SingleLine(original));
   }
 }
 static void ReadSpatialBend(StringBuilder output, IModelDoc2 model, IFace2 face, ISurface surface) {
   IFeature owner = face.GetFeature() as IFeature;
   if (owner == null || owner.GetTypeName2() != "SketchBend") return;
   double[] cylinder = surface.CylinderParams as double[];
   Array edges = face.GetEdges() as Array;
   if (cylinder == null || edges == null) return;
   double angle = 0;
   foreach (IEdge edge in edges) {
     ICurve curve = edge.GetCurve() as ICurve;
     if (curve == null || !curve.IsCircle()) continue;
     double[] arc = curve.CircleParams as double[];
     ICurveParamData trim = edge.GetCurveParams3();
     if (arc == null || trim == null || Math.Abs(arc[6]-cylinder[6]) > 1e-7) continue;
     double parallel = arc[3]*cylinder[3] + arc[4]*cylinder[4] + arc[5]*cylinder[5];
     if (Math.Abs(Math.Abs(parallel)-1) > 1e-6) continue;
     angle = Math.Max(angle, Math.Abs(trim.UMaxValue-trim.UMinValue));
   }
   if (angle > 0 && angle < 2*Math.PI-1e-6)
     output.AppendLine("SpatialBend: " + PersistentId(model, owner) + " | " + SingleLine(owner.Name) +
                       " | " + PersistentId(model, face) + " | " + Fmt(new double[] { angle, cylinder[6] }));
 }
 static List<Opening> ReadModelFeatures(StringBuilder output, IView view, Dictionary<string,List<Opening>> inventories,
                                       IMathUtility math) {
   try {
     Console.WriteLine("Model inventory: resolving referenced document");
     IModelDoc2 model = view.ReferencedDocument as IModelDoc2;
     if (model == null) return null;
     string config = view.ReferencedConfiguration;
     string active = model.ConfigurationManager.ActiveConfiguration.Name;
     bool current = model is IPartDoc && String.Equals(config, active, StringComparison.OrdinalIgnoreCase);
     output.AppendLine("PartViewInventory: " + current);
     string identity = model.GetPathName() + " | " + config;
     List<Opening> openings;
     if (inventories.TryGetValue(identity, out openings)) return current ? openings : null;
     output.AppendLine("ModelFeatureInventory: " + identity);
     Console.WriteLine("Model inventory: enumerating feature tree");
     IFeature feature = model.FirstFeature() as IFeature;
     while (feature != null) {
       Array states = String.IsNullOrEmpty(config) ? null : feature.IsSuppressed2(3, new string[] { config }) as Array;
       string suppressed = states != null && states.Length == 1 ? Convert.ToString(states.GetValue(0)) : "unknown";
       string featureType = feature.GetTypeName2();
       output.AppendLine("ModelFeature: " + featureType + " | " + SingleLine(feature.Name) + " | suppressed=" + suppressed);
       if (current && suppressed == "False" && (featureType == "LPattern" || featureType == "CirPattern"))
         ReadPatternSeeds(output,model,feature,featureType);
       if (current && suppressed == "False" && featureType == "HoleWzd")
         ReadWizardHole(output,model,feature,math);
       feature = feature.GetNextFeature() as IFeature;
     }
     openings = new List<Opening>();
     if (current && !SkipBodyOpenings) {
       Console.WriteLine("Model inventory: reading body faces and openings");
       ReadCutoutOpenings(output, model, openings);
     }
     if (current && SkipBodyOpenings)
       output.AppendLine("ModelOpeningInventoryReadError: skipped after full extraction timed out");
     inventories.Add(identity, current ? openings : null);
     return current ? openings : null;
   } catch (Exception ex) { output.AppendLine("ModelFeatureReadError: " + ex.Message); }
   return null;
 }
 static void ReadWizardHole(StringBuilder output, IModelDoc2 model, IFeature feature, IMathUtility math) {
   try {
     IWizardHoleFeatureData2 hole = feature.GetDefinition() as IWizardHoleFeatureData2;
     string id = PersistentId(model,feature);
     if (hole == null || id.Length == 0) return;
     output.AppendLine("ModelWizardHole: " + id + " | " + SingleLine(feature.Name) +
                       " | type=" + hole.Type + " | size=" + SingleLine(hole.FastenerSize) +
                       " | points=" + hole.GetSketchPointCount() + " | end=" + hole.EndCondition);
     output.AppendLine("ModelWizardHoleParameters: " + id + " | " + Fmt(new double[] {
       hole.HoleDiameter, hole.Diameter, hole.ThruHoleDiameter, hole.TapDrillDiameter,
       hole.ThreadDiameter, hole.CounterBoreDiameter, hole.CounterSinkDiameter,
       hole.HoleDepth, hole.Depth, hole.ThreadDepth, hole.CounterBoreDepth }));
     Array points = hole.GetSketchPoints() as Array;
     if (points == null) return;
     foreach (ISketchPoint point in points) {
       double[] original = { point.X, point.Y, point.Z };
       ISketch sketch = point.GetSketch() as ISketch;
       IMathTransform transform = sketch == null ? null : sketch.ModelToSketchTransform;
       IMathPoint position = math.CreatePoint(original) as IMathPoint;
       if (position != null && transform != null)
         position = position.MultiplyTransform(transform.Inverse()) as IMathPoint;
       output.AppendLine("ModelWizardHolePoint: " + id + " | " + Fmt(original) + " | " +
                         Fmt(position == null ? null : position.ArrayData));
     }
   } catch (Exception ex) { output.AppendLine("WizardHoleReadError: " + SingleLine(feature.Name) + " | " + SingleLine(ex.Message)); }
 }
 static void ReadPatternSeeds(StringBuilder output, IModelDoc2 model, IFeature feature, string featureType) {
   try {
     object definition = feature.GetDefinition();
     ILinearPatternFeatureData linear = definition as ILinearPatternFeatureData;
     ICircularPatternFeatureData circular = definition as ICircularPatternFeatureData;
     Array seeds = linear != null ? linear.PatternFeatureArray as Array :
                   circular != null ? circular.PatternFeatureArray as Array : null;
     string pattern = PersistentId(model,feature);
     if (seeds == null || pattern.Length == 0) return;
     foreach (object item in seeds) {
       IFeature seed = item as IFeature;
       string id = seed == null ? "" : PersistentId(model,seed);
       if (id.Length > 0)
         output.AppendLine("ModelPatternSeed: " + pattern + " | " + id + " | " + featureType);
     }
   } catch (Exception ex) { output.AppendLine("PatternReadError: " + SingleLine(ex.Message)); }
 }
 static void ReadViewDimensionContext(StringBuilder output, IView view, IModelDoc2 drawing) {
   try {
     int count;
     int breaks = view.GetBreakLineCount2(out count);
     IView parent = view.GetBaseView() as IView;
     output.AppendLine("ViewDimensionContext: cropped=" + view.IsCropped() + " | breakLines=" + breaks +
                       " | breakOuts=" + view.GetBreakOutSectionCount() + " | modelBreak=" + view.IsModelBreakState() +
                       " | baseView=" + SingleLine(parent == null ? "" : parent.GetName2()));
   } catch (Exception ex) { output.AppendLine("ViewDimensionContextReadError: " + SingleLine(ex.Message)); }
   try {
     IModelDoc2 model = view.ReferencedDocument as IModelDoc2;
     IPartDoc part = model as IPartDoc;
     if (part == null || !String.Equals(view.ReferencedConfiguration, model.ConfigurationManager.ActiveConfiguration.Name,
                                       StringComparison.OrdinalIgnoreCase)) {
       output.AppendLine("ViewEnvelopeStatus: unavailable-model-or-configuration");
     } else {
       Array bodies = part.GetBodies2(0, false) as Array;
       double[] transform = view.ModelToViewTransform.ArrayData as double[];
       if (bodies == null || bodies.Length == 0 || transform == null || transform.Length < 13)
         output.AppendLine("ViewEnvelopeStatus: unavailable-bodies-or-transform");
       else {
         int resolved = 0;
         for (int axis = 0; axis < 2; axis++) for (int sign = -1; sign <= 1; sign += 2) {
           double dx = sign*transform[axis], dy = sign*transform[3+axis], dz = sign*transform[6+axis];
           double best = Double.NegativeInfinity;
           double[] point = null;
           bool complete = true;
           foreach (IBody2 body in bodies) {
             double x, y, z;
             if (body == null || !body.GetExtremePoint(dx,dy,dz,out x,out y,out z)) { complete = false; break; }
             double score = dx*x+dy*y+dz*z;
             if (score > best) { best = score; point = new double[] { x,y,z }; }
           }
           if (complete && point != null) {
             resolved++;
             output.AppendLine("ViewEnvelopeExtreme: " + axis + " | " + sign + " | " + Fmt(point));
           }
         }
         output.AppendLine("ViewEnvelopeStatus: " + (resolved == 4 ? "complete" : "incomplete") +
                           " | solidBodies=" + bodies.Length);
       }
     }
   } catch (Exception ex) { output.AppendLine("ViewEnvelopeReadError: " + SingleLine(ex.Message)); }
   try {
     int index = 0;
     for (ICenterLine line = view.GetFirstCenterLine() as ICenterLine; line != null; line = line.GetNext() as ICenterLine)
       ReadViewCenter(output, drawing, line.GetAnnotation() as IAnnotation, "centerline", index++);
     index = 0;
     Array marks = view.GetCenterMarks() as Array;
     if (marks != null) foreach (ICenterMark mark in marks) {
       int current = index++;
       ReadViewCenter(output, drawing, mark.GetAnnotation() as IAnnotation, "centerMark", current);
       int count = mark.IsGrouped ? mark.GroupCount : 1;
       for (int i = 0; i < count; i++)
         output.AppendLine("ViewCenterMarkPoint: " + current + " | " + Fmt(mark.GetPosition(i)));
     }
     output.AppendLine("ViewCenterInventoryEnd");
   } catch (Exception ex) { output.AppendLine("ViewCenterReadError: " + SingleLine(ex.Message)); }
 }
 static void ReadViewCenter(StringBuilder output, IModelDoc2 drawing, IAnnotation annotation, string kind, int index) {
   if (annotation == null) return;
   output.AppendLine("ViewCenter: " + kind + " | " + index + " | " + annotation.Visible + " | " +
                     PersistentId(drawing, annotation) + " | " + SingleLine(annotation.GetName()));
   IDisplayData display = annotation.GetDisplayData() as IDisplayData;
   if (display != null) for (int i = 0; i < display.GetLineCount(); i++)
     output.AppendLine("ViewCenterDisplayLine: " + kind + " | " + index + " | " + Fmt(display.GetLineAtIndex2(i)));
   Array entities = annotation.GetAttachedEntities3() as Array;
   if (entities != null) for (int i = 0; i < entities.Length; i++) {
     object entity = entities.GetValue(i);
     if (entity != null)
       output.AppendLine("ViewCenterAttachment: " + kind + " | " + index + " | " + i + " | " + PersistentId(drawing, entity));
   }
 }
 static void ReadDimensionGeometry(StringBuilder output, IDimension dimension, IAnnotation annotation, IDisplayData display, IModelDoc2 drawing, IView view, List<Opening> openings) {
   try {
     Array points = dimension.ReferencePoints as Array;
     if (points != null) foreach (object point in points) {
       IMathPoint mathPoint = point as IMathPoint;
       if (mathPoint != null) output.AppendLine("DimensionReferencePoint: " + Fmt(mathPoint.ArrayData));
       else if (point != null) output.AppendLine("DimensionReferenceValue: " + Fmt(point));
     }
   } catch (Exception ex) { output.AppendLine("ReferencePointReadError: " + ex.Message); }
   try {
     for (int i = 0; i < display.GetLineCount(); i++)
       output.AppendLine("DimensionDisplayLine: " + Fmt(display.GetLineAtIndex2(i)));
     for (int i = 0; i < display.GetArrowHeadCount(); i++)
       output.AppendLine("DimensionArrowHead: " + Fmt(display.GetArrowHeadAtIndex2(i)));
     for (int i = 0; i < annotation.GetLeaderCount(); i++)
       output.AppendLine("DimensionLeader: " + Fmt(annotation.GetLeaderPointsAtIndex(i)));
   } catch (Exception ex) { output.AppendLine("DisplayGeometryReadError: " + ex.Message); }
   Array entities = annotation.GetAttachedEntities3() as Array;
   if (entities == null) return;
   Array types = annotation.GetAttachedEntityTypes() as Array;
   int index = 0;
   foreach (object entity in entities) {
     if (entity != null) {
       try {
         ICenterMark mark = entity as ICenterMark;
         if (mark != null) {
           int count = mark.IsGrouped ? mark.GroupCount : 1;
           for (int i = 0; i < count; i++)
             output.AppendLine("AttachedCenterMark: " + index + " | " + Fmt(mark.GetPosition(i)));
         }
         ISketchPoint sketchPoint = entity as ISketchPoint;
         if (sketchPoint != null)
           output.AppendLine("AttachedSketchPoint: " + index + " | " + SketchPointCoordinates(sketchPoint));
         ISketchSegment segment = entity as ISketchSegment;
         if (segment != null) ReadAttachedSketchSegment(output, segment, index);
         IModelDoc2 referenced = view.ReferencedDocument as IModelDoc2;
         int selectionType = types != null && index < types.Length ? Convert.ToInt32(types.GetValue(index)) : -1;
         IEntity modelEntity = entity as IEntity;
         IComponent2 component = modelEntity == null ? null : modelEntity.IGetComponent2() as IComponent2;
         output.AppendLine("DimensionAttachmentContext: " + index + " | " + selectionType + " | " +
                           SingleLine(component == null ? "" : component.Name2) + " | " +
                           SingleLine(component == null ? "" : component.ReferencedConfiguration));
         if (selectionType == 39) // swSelCTHREADS
           ReadThreadAttachment(output, entity, index, drawing, referenced);
         if (selectionType == 103) // swSelCENTERLINES
           ReadCenterlineAttachment(output, entity, index, drawing);
         IVertex vertex = entity as IVertex;
         if (vertex != null)
           output.AppendLine("IndexedDimensionVertex: " + index + " | " + Fmt(vertex.GetPoint()));
         ISilhouetteEdge silhouette = entity as ISilhouetteEdge;
         if (silhouette != null) {
           IMathPoint first = silhouette.GetStartPoint(), last = silhouette.GetEndPoint();
           if (first != null && last != null && silhouette.GetFace() != null)
             output.AppendLine("IndexedDimensionSilhouette: " + index + " | " +
                               Fmt(first.ArrayData) + Fmt(last.ArrayData));
         }
         IEdge edge = entity as IEdge;
         ICurve curve = edge == null ? null : edge.GetCurve() as ICurve;
         if (curve != null)
           output.AppendLine("IndexedDimensionModelEdge: " + index + " | " + curve.Identity());
         if (curve != null && curve.IsLine()) {
           ICurveParamData trim = edge.GetCurveParams3();
           if (trim != null) {
             string coords = Fmt(trim.StartPoint) + Fmt(trim.EndPoint);
             output.AppendLine("DimensionStraightEdge: " + coords);
             output.AppendLine("IndexedDimensionStraightEdge: " + index + " | " + coords);
           }
         }
         if (curve != null && curve.IsCircle() && referenced != null) {
           output.AppendLine("DimensionCircularEdge: " + PersistentId(referenced, edge) + " | " + Fmt(curve.CircleParams));
           output.AppendLine("IndexedDimensionCircularEdge: " + index + " | " + Fmt(curve.CircleParams));
           string matchMethod;
           Opening opening = MatchOpening(referenced, edge, curve.CircleParams as double[], openings, out matchMethod);
           if (opening != null)
             output.AppendLine("DimensionCircularOpening: " + opening.WallId + " | " + matchMethod + " | " + Fmt(opening.Circle));
         }
         if (referenced != null) ReadPersistentReference(output, referenced, entity, index, "model");
         ReadPersistentReference(output, drawing, entity, index, "drawing");
       } catch (Exception ex) { output.AppendLine("AttachmentGeometryReadError: " + index + " | " + ex.Message); }
     } else output.AppendLine("DimensionAttachmentNull: " + index);
     index++;
   }
 }
 static void ReadThreadAttachment(StringBuilder output, object entity, int index, IModelDoc2 drawing, IModelDoc2 model) {
   try {
     ICThread thread = entity as ICThread;
     IAnnotation annotation = entity as IAnnotation;
     if (thread == null && annotation != null) thread = annotation.GetSpecificAnnotation() as ICThread;
     if (thread == null) {
       IFeature feature = entity as IFeature;
       if (feature != null) {
         ICosmeticThreadFeatureData data = feature.GetDefinition() as ICosmeticThreadFeatureData;
         IEdge edge = data == null ? null : data.Edge;
         if (edge != null) ReadThreadEdge(output,index,edge,model);
         thread = feature.GetSpecificFeature2() as ICThread;
       }
     }
     if (thread == null) return;
     if (annotation == null) annotation = thread.GetAnnotation() as IAnnotation;
     if (annotation == null) return;
     output.AppendLine("DimensionThreadSymbol: " + index + " | " + PersistentId(drawing,annotation) +
                       " | " + SingleLine(annotation.GetName()));
     Array entities = annotation.GetAttachedEntities3() as Array;
     if (entities != null) foreach (object item in entities) {
       IEdge edge = item as IEdge;
       if (edge != null) ReadThreadEdge(output,index,edge,model);
     }
     IDisplayData dataDisplay = thread.GetDisplayData() as IDisplayData;
     if (dataDisplay != null) for (int i=0;i<dataDisplay.GetArcCount();i++)
       output.AppendLine("DimensionThreadDisplayArc: " + index + " | " + Fmt(dataDisplay.GetArcAtIndex2(i)));
   } catch (Exception ex) { output.AppendLine("ThreadAttachmentReadError: " + index + " | " + SingleLine(ex.Message)); }
 }
 static void ReadThreadEdge(StringBuilder output, int index, IEdge edge, IModelDoc2 model) {
   ICurve curve = edge.GetCurve() as ICurve;
   if (curve != null && curve.IsCircle())
     output.AppendLine("DimensionThreadModelCircle: " + index + " | " +
                       (model == null ? "" : PersistentId(model,edge)) + " | " + Fmt(curve.CircleParams));
 }
 static void ReadCenterlineAttachment(StringBuilder output, object entity, int index, IModelDoc2 drawing) {
   try {
     ICenterLine line = entity as ICenterLine;
     IAnnotation annotation = entity as IAnnotation;
     if (line == null && annotation != null) line = annotation.GetSpecificAnnotation() as ICenterLine;
     if (line == null) return;
     if (annotation == null) annotation = line.GetAnnotation() as IAnnotation;
     if (annotation == null) return;
     output.AppendLine("AttachedCenterline: " + index + " | " + PersistentId(drawing,annotation) +
                       " | " + SingleLine(annotation.GetName()));
     IDisplayData display = annotation.GetDisplayData() as IDisplayData;
     if (display != null) for (int i=0;i<display.GetLineCount();i++)
       output.AppendLine("AttachedCenterlineDisplayLine: " + index + " | " + Fmt(display.GetLineAtIndex2(i)));
   } catch (Exception ex) { output.AppendLine("CenterlineAttachmentReadError: " + index + " | " + SingleLine(ex.Message)); }
 }
 static string SketchPointCoordinates(ISketchPoint point) {
   return Fmt(new double[] { point.X, point.Y, point.Z });
 }
 static void ReadAttachedSketchSegment(StringBuilder output, ISketchSegment segment, int index) {
   try {
     ISketch sketch = segment.GetSketch() as ISketch;
     IMathTransform transform = sketch == null ? null : sketch.ModelToSketchTransform;
     output.AppendLine("AttachedSketchSegment: " + index + " | type=" + segment.GetType() +
                       " | construction=" + segment.ConstructionGeometry + " | id=" + Fmt(segment.GetID()));
     if (transform != null)
       output.AppendLine("AttachedSketchTransform: " + index + " | " + Fmt(transform.ArrayData));
     ISketchLine line = segment as ISketchLine;
     if (line != null) {
       ISketchPoint start = line.GetStartPoint2() as ISketchPoint;
       ISketchPoint end = line.GetEndPoint2() as ISketchPoint;
       if (start != null && end != null)
         output.AppendLine("AttachedSketchLine: " + index + " | " + SketchPointCoordinates(start) + SketchPointCoordinates(end));
     }
     ISketchArc arc = segment as ISketchArc;
     if (arc != null) {
       ISketchPoint center = arc.GetCenterPoint2() as ISketchPoint;
       if (center != null)
         output.AppendLine("AttachedSketchArc: " + index + " | " + SketchPointCoordinates(center) + arc.GetRadius());
     }
   } catch (Exception ex) { output.AppendLine("AttachedSketchReadError: " + index + " | " + ex.Message); }
 }
 static void ReadPersistentReference(StringBuilder output, IModelDoc2 owner, object entity, int index, string scope) {
   try {
     byte[] reference = owner.Extension.GetPersistReference3(entity) as byte[];
     if (reference != null && reference.Length > 0)
       output.AppendLine("AttachmentPersistentReference: " + index + " | " + scope + " | " + Convert.ToBase64String(reference));
   } catch (Exception ex) { output.AppendLine("PersistentReferenceReadError: " + index + " | " + scope + " | " + ex.Message); }
 }
 static double[] SheetPoint(IMathUtility math, double x, double y, IMathTransform toModel, IMathTransform toSheet) {
   IMathPoint point = (IMathPoint)math.CreatePoint(new double[] { x, y, 0 });
   if (toModel != null) point = (IMathPoint)point.MultiplyTransform(toModel);
   if (toSheet != null) point = (IMathPoint)point.MultiplyTransform(toSheet);
   return (double[])point.ArrayData;
 }
 static void ReadGraphicObjects(StringBuilder output, string scope, IMathUtility math,
                               ISketch sketch, Array pictures, int expectedPictures, IMathTransform toSheet) {
   try {
     IMathTransform modelToSketch = sketch == null ? null : sketch.ModelToSketchTransform;
     IMathTransform toModel = modelToSketch == null ? null : (IMathTransform)modelToSketch.Inverse();
     int count = pictures == null ? 0 : pictures.Length;
     Array blocks = sketch == null ? null : sketch.GetSketchBlockInstances() as Array;
     output.AppendLine("GraphicInventory: " + scope + " | pictures=" + count +
                       " | blocks=" + (blocks == null ? 0 : blocks.Length));
     if (count != expectedPictures || (sketch != null && sketch.GetSketchBlockInstanceCount() != (blocks == null ? 0 : blocks.Length)))
       output.AppendLine("GraphicReadError: " + scope + " | count does not match inventory");
     if (pictures != null) for (int i = 0; i < pictures.Length; i++) {
       ISketchPicture picture = (ISketchPicture)pictures.GetValue(i);
       double x=0, y=0, width=0, height=0;
       picture.GetOrigin(ref x, ref y);
       picture.GetSize(ref width, ref height);
       double cosine = Math.Cos(picture.Angle), sine = Math.Sin(picture.Angle);
       var points = new List<double[]>();
       foreach (double u in new double[] { 0, width })
         foreach (double v in new double[] { 0, height })
           points.Add(SheetPoint(math, x+u*cosine-v*sine, y+u*sine+v*cosine, toModel, toSheet));
       double x0=Double.PositiveInfinity, y0=x0, x1=Double.NegativeInfinity, y1=x1;
       foreach (double[] point in points) {
         x0=Math.Min(x0,point[0]); y0=Math.Min(y0,point[1]);
         x1=Math.Max(x1,point[0]); y1=Math.Max(y1,point[1]);
       }
       output.AppendLine("GraphicPicture: " + scope + " | " + i + " | " + Fmt(new double[] { x0,y0,x1,y1 }));
     }
     if (blocks != null) for (int i = 0; i < blocks.Length; i++) {
       ISketchBlockInstance block = (ISketchBlockInstance)blocks.GetValue(i);
       if (block.Visible == 2 || block.Visible == 3) continue;
       IMathPoint position = block.InstancePosition;
       double[] local = position == null ? null : position.ArrayData as double[];
       output.AppendLine("GraphicBlock: " + scope + " | " + i + " | " +
                         (local == null ? "null" : Fmt(SheetPoint(math,local[0],local[1],toModel,toSheet))) +
                         " | " + SingleLine(block.Name));
       ISketchBlockDefinition definition = block.Definition;
       Array notes = definition == null ? null : definition.GetNotes() as Array;
       if (notes != null) foreach (INote note in notes)
         output.AppendLine("GraphicBlockText: " + scope + " | " + i + " | " + SingleLine(note.GetText()));
     }
   } catch (Exception ex) { output.AppendLine("GraphicReadError: " + scope + " | " + SingleLine(ex.Message)); }
 }
 static void ReadSheetOle(StringBuilder output, IModelDoc2 model) {
   try {
     const int currentSheet = 1; // swOleObjectOptions_GetOnCurrentSheet
     Array objects = model.Extension.GetOLEObjects(currentSheet) as Array;
     int count = model.Extension.GetOLEObjectCount(currentSheet);
     output.AppendLine("GraphicOleCount: " + count);
     if (count != (objects == null ? 0 : objects.Length))
       output.AppendLine("GraphicReadError: OLE | count does not match inventory");
     if (objects != null) for (int i = 0; i < objects.Length; i++) {
       ISwOLEObject item = (ISwOLEObject)objects.GetValue(i);
       output.AppendLine("GraphicOle: " + i + " | " + Fmt(item.Boundaries) + " | " + SingleLine(item.Clsid));
     }
   } catch (Exception ex) { output.AppendLine("GraphicReadError: OLE | " + SingleLine(ex.Message)); }
 }
 static string PropertyColumn(string value) { return SingleLine(value).Replace("\t","\\t"); }
 static void ReadPropertyScope(StringBuilder output, IModelDoc2 model, string configuration, string scope) {
   ICustomPropertyManager properties = model.Extension.get_CustomPropertyManager(configuration);
   if (properties == null) return;
   string[] fields = { "\u673a\u578b", "\u540d\u79f0", "\u96f6\u4ef6\u540d\u79f0", "\u6750\u6599",
                       "\u4ee3\u53f7", "\u56fe\u53f7", "\u7269\u6599\u7f16\u7801",
                       "\u6570\u91cf", "\u7248\u6b21" };
   foreach (string field in fields) {
     string raw, evaluated;
     bool resolved, linked;
     int result = properties.Get6(field,true,out raw,out evaluated,out resolved,out linked);
     output.AppendLine("TitleProperty: " + String.Join("\t",new string[] {
       scope, field, result.ToString(), resolved.ToString(), linked.ToString(),
       PropertyColumn(raw), PropertyColumn(evaluated) }));
   }
 }
 static void ReadTitlePropertySource(StringBuilder output, IModelDoc2 drawingModel, IDrawingDoc drawing, ISheet sheet) {
   try {
     ReadPropertyScope(output,drawingModel,"","drawing");
     string requested = sheet.CustomPropertyView ?? "";
     IView sheetView = drawing.GetFirstView() as IView;
     IView first = sheetView == null ? null : sheetView.GetNextView() as IView;
     IView chosen = null;
     for (IView view=first; view!=null; view=view.GetNextView() as IView)
       if (String.Equals(view.GetName2(),requested,StringComparison.OrdinalIgnoreCase)) { chosen=view; break; }
     if (chosen == null && (String.IsNullOrEmpty(requested) || requested == "\u9ed8\u8ba4" ||
                            String.Equals(requested,"Default",StringComparison.OrdinalIgnoreCase)))
       chosen = first;
     IModelDoc2 source = chosen == null ? null : chosen.ReferencedDocument as IModelDoc2;
     string configuration = chosen == null ? "" : chosen.ReferencedConfiguration;
     output.AppendLine("TitlePropertySource: " + String.Join("\t",new string[] {
       PropertyColumn(requested), PropertyColumn(chosen == null ? "" : chosen.GetName2()),
       PropertyColumn(source == null ? "" : source.GetPathName()), PropertyColumn(configuration) }));
     if (source != null) {
       if (!String.IsNullOrEmpty(configuration)) ReadPropertyScope(output,source,configuration,"configuration");
       ReadPropertyScope(output,source,"","model");
     }
   } catch (Exception ex) { output.AppendLine("TitlePropertyReadError: " + SingleLine(ex.Message)); }
 }
 static string Fmt(object o) { if(o==null)return "null"; var a=o as Array; if(a==null)return o.ToString(); var b=new StringBuilder(); foreach(var x in a)b.Append(x+","); return b.ToString(); } public static string Run(string path) {
 var b=new StringBuilder();
 Thread.CurrentThread.CurrentCulture = CultureInfo.InvariantCulture;
 ISldWorks sw=(ISldWorks)Activator.CreateInstance(Type.GetTypeFromProgID("SldWorks.Application"));
 b.AppendLine("Version: "+sw.RevisionNumber());
 int e=0,w=0;
 Console.WriteLine("Inventory: opening drawing");
 IModelDoc2 model=sw.OpenDoc6(path,3,3,"",ref e,ref w);
 b.AppendLine("Open errors="+e+" warnings="+w);
 if(model==null) return b.ToString();
 b.AppendLine("Title: "+model.GetTitle());
 b.AppendLine("DrawingUnits: "+Fmt(model.GetUnits()));
 IDrawingDoc drawing=(IDrawingDoc)model;
 foreach(string sheet in (string[])drawing.GetSheetNames()) b.AppendLine("Sheet: "+sheet);
 ISheet sh=(ISheet)drawing.GetCurrentSheet();
Console.WriteLine("Inventory: reading sheet format");
double width=0,height=0; sh.GetSize(ref width,ref height);
b.AppendLine("SheetSize_m: "+width+","+height+" Template: "+sh.GetTemplateName());
b.AppendLine("SheetProperties: "+Fmt(sh.GetProperties2()));
b.AppendLine("SheetFormatVisible: "+sh.SheetFormatVisible);
ISketch sk=sh.GetTemplateSketch();
if(sk!=null) { object[] seg=sk.GetSketchSegments() as object[]; b.AppendLine("TemplateSegments: "+(seg==null?0:seg.Length)); if(seg!=null) foreach(object so in seg) { ISketchLine line=so as ISketchLine; if(line!=null) { ISketchPoint sp=(ISketchPoint)line.GetStartPoint2(), ep=(ISketchPoint)line.GetEndPoint2(); b.AppendLine("FrameLine: "+sp.X+","+sp.Y+","+sp.Z+" -> "+ep.X+","+ep.Y+","+ep.Z); } } b.AppendLine("TemplatePictures: "+sk.GetSketchPictureCount()+" Blocks: "+sk.GetSketchBlockInstanceCount()); }
IMathUtility math = (IMathUtility)sw.GetMathUtility();
ReadSheetOle(b,model);
if(sk!=null) try {
  ReadGraphicObjects(b,"template",math,sk,sk.GetSketchPictures() as Array,sk.GetSketchPictureCount(),null);
} catch (Exception ex) { b.AppendLine("GraphicReadError: template | " + SingleLine(ex.Message)); }
 IView v=(IView)drawing.GetFirstView();
 ILayerMgr layers = model.GetLayerManager() as ILayerMgr;
 var inventories = new Dictionary<string,List<Opening>>(StringComparer.OrdinalIgnoreCase);
 var spatialInventories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
 while(v!=null) {
 Console.WriteLine("Inventory view: " + v.GetName2() + " - metadata and tables");
 string referencedPath = v.GetReferencedModelName();
 string referencedConfiguration = v.ReferencedConfiguration;
 b.AppendLine("View: "+v.GetName2()+" Model: "+referencedPath);
 b.AppendLine("ViewMetadata: type="+v.Type+" | scale="+v.ScaleDecimal+" | configuration="+referencedConfiguration);
 if (v.Type != 1) try { b.AppendLine("ViewOutline: "+Fmt(v.GetOutline())); }
 catch (Exception ex) { b.AppendLine("ViewOutlineReadError: "+SingleLine(ex.Message)); }
 if (v.Type == 3) { // swDrawingDetailView
   IView parent = v.GetBaseView() as IView;
   if (parent != null) b.AppendLine("DetailBaseView: " + parent.GetName2());
 }
 ReadTables(b,v);
 Console.WriteLine("Inventory view: reading model features");
 int modelRecordsStart = b.Length;
 List<Opening> openings = ReadModelFeatures(b,v,inventories,math);
 if (v.Type != 1 && !String.IsNullOrEmpty(referencedPath)) {
   string spatialKey = referencedPath + "\n" + referencedConfiguration;
   if (!spatialInventories.Contains(spatialKey)) {
     Console.WriteLine("Model inventory: reading spatial geometry");
     var spatial = new StringBuilder();
     string openingInventory = openings == null ? null : b.ToString(modelRecordsStart, b.Length-modelRecordsStart);
     if (openingInventory != null && !openingInventory.Contains("ModelCylinderInventoryEnd"))
       openingInventory = null;
     ReadSpatialModel(spatial,v,math,openingInventory);
     string records = spatial.ToString();
     b.Append(records);
     if (records.Contains("SpatialRoot: ") && !records.Contains("SpatialModelReadError:"))
       spatialInventories.Add(spatialKey);
   }
 }
 object[] notes=v.GetNotes() as object[];
 Console.WriteLine("Inventory view: reading visible edges");
 if (!SkipBodyOpenings) ReadVisibleEdges(b,v);
 else b.AppendLine("INVENTORY_ERROR: visible edges skipped in reduced extraction");
 b.AppendLine("ViewTransform: "+Fmt(v.ModelToViewTransform.ArrayData)); b.AppendLine("SketchPictures: "+v.GetSketchPictureCount());
 if (v.Type != 1) {
   Console.WriteLine("Inventory view: reading dimension context");
   ReadViewDimensionContext(b,v,model);
 }
 try {
   ReadGraphicObjects(b,SingleLine(v.GetName2()),math,v.GetSketch() as ISketch,
                      v.GetSketchPictures() as Array,v.GetSketchPictureCount(),
                      v.Type == 1 ? null : v.ModelToViewTransform);
 } catch (Exception ex) { b.AppendLine("GraphicReadError: view | " + SingleLine(ex.Message)); }
if(notes!=null) foreach(INote n in notes) {
 b.AppendLine("Note: "+n.GetText());
 b.AppendLine("NoteExtent: "+Fmt(n.GetExtent())+" Linked: "+n.PropertyLinkedText);
 try {
   IAnnotation annotation = n.GetAnnotation() as IAnnotation;
   if (annotation != null) {
     string layerName = annotation.Layer;
     ILayer layer = layers == null || String.IsNullOrEmpty(layerName) ? null : layers.GetLayer(layerName) as ILayer;
     string layerState = layer != null ? (layer.Visible ? "visible" : "hidden") :
                         String.IsNullOrEmpty(layerName) || layerName == "-None-" ? "visible" : "unknown";
     b.AppendLine("NoteMetadata: visibility=" + annotation.Visible + " | layer=" + layerState +
                  " | owner=" + annotation.OwnerType +
                  " | name=" + SingleLine(annotation.GetName()));
   }
 } catch (Exception ex) { b.AppendLine("NoteMetadataReadError: " + SingleLine(ex.Message)); }
}
 Console.WriteLine("Inventory view: reading dimensions");
 IDisplayDimension dim=v.GetFirstDisplayDimension5(); int count=0;
 while(dim!=null && count<1000) { count++;
try {
IDimension d=(IDimension)dim.GetDimension2(0);
IAnnotation a=(IAnnotation)dim.GetAnnotation();
b.AppendLine("DIM: "+d.FullName+" | SystemValue: "+d.SystemValue+" | Type: "+dim.Type2+" | Reference: "+dim.IsReferenceDim()+" | ToleranceType: "+d.GetToleranceType()+" | Tolerance: "+Fmt(d.GetToleranceValues()));
try { b.AppendLine("DimensionState: driven="+d.DrivenState+" | visibility="+a.Visible+" | annotationId="+PersistentId(model,a)); }
catch (Exception ex) { b.AppendLine("DimensionStateReadError: "+SingleLine(ex.Message)); }
b.AppendLine("Parentheses: "+dim.ShowParenthesis); object[] attached=a.GetAttachedEntities3() as object[]; if(attached!=null) foreach(object ent in attached) { IEdge edge=ent as IEdge; IVertex vertex=ent as IVertex; if(edge!=null) { ICurve c=(ICurve)edge.GetCurve(); b.AppendLine("AttachedEdge: line="+c.IsLine()+" circle="+c.IsCircle()+" params="+Fmt(c.IsCircle()?c.CircleParams:c.IsLine()?c.LineParams:null)); } else if(vertex!=null)b.AppendLine("AttachedVertex: "+Fmt(vertex.GetPoint())); else b.AppendLine("AttachedOther: "+(ent==null?"null":ent.GetType().Name)); } IDisplayData display=(IDisplayData)dim.GetDisplayData(); for(int ti=0;ti<display.GetTextCount();ti++) b.AppendLine("RenderedDimText: "+display.GetTextAtIndex(ti)+" position="+Fmt(display.GetTextPositionAtIndex(ti))); b.AppendLine("RenderedLines: "+display.GetLineCount()); b.AppendLine("DimTextParts: "+dim.GetText(0)+" | "+dim.GetText(1)+" | "+dim.GetText(2)+" | "+dim.GetText(3));
b.AppendLine("DimPosition: "+Fmt(a.GetPosition())+" | Dangling: "+a.IsDangling()+" | AttachedCount: "+a.GetAttachedEntityCount3()+" | AttachedTypes: "+Fmt(a.GetAttachedEntityTypes())+" | ReferencePoints: "+d.GetReferencePointsCount());
ReadDimensionGeometry(b,d,a,display,model,v,openings);
} catch(Exception ex) {b.AppendLine("DIM_ERROR: "+ex.Message);}
dim=dim.GetNext5(); }
 b.AppendLine("DisplayDimensions: "+count);
 v=(IView)v.GetNextView();
 }
 Console.WriteLine("Inventory: reading title properties");
 ReadTitlePropertySource(b,model,drawing,sh);
 return b.ToString();
 }
}
"@
[SwInventory]::Run($args[0])




