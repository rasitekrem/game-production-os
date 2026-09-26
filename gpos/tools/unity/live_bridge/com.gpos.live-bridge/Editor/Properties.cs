// GPOS live bridge — serialized properties of one Component: bounded enumeration and the default-deny write.
// Enumeration lists visible properties only (and a Behaviour's m_Enabled); hidden serialized state is covered by
// the component token but never listed, valued or written. It never enters arrays, managed references or the
// children of non-generic values. A write is allowed only for a listed property whose path, ancestry and
// (property type, type name) the core PropertyRules accept; the value is validated first and read back after.
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;

namespace Gpos.LiveBridge
{
    internal static class Properties
    {
        public const int PageSize = 256;
        public const int MaxWalk = 20000;
        public const int MaxEnumNames = 64;
        public const int ListedString = 256;

        static HashSet<string> VisiblePaths(SerializedObject so)
        {
            var visible = new HashSet<string>(StringComparer.Ordinal);
            var it = so.GetIterator();
            int n = 0;
            while (it.NextVisible(true))
            {
                if (++n > MaxWalk) throw new Refusal("AUTHORING_LIMIT", "the component has more than " + MaxWalk + " serialized properties");
                visible.Add(it.propertyPath);
            }
            return visible;
        }

        static bool Listed(Component c, string path, HashSet<string> visible)
        {
            return visible.Contains(path) || (path == "m_Enabled" && c is Behaviour);
        }

        // Why this property may not be written ("TRANSFORM_USES_SET_TRANSFORM", "PREFAB_BOUNDARY", "HIDDEN",
        // "PATH_UNSUPPORTED", "DENIED", "INSIDE_UNSUPPORTED", "NOT_EDITABLE", "TYPE_UNSUPPORTED"), or null.
        public static string WriteRefusal(Component c, SerializedObject so, SerializedProperty p, HashSet<string> visible)
        {
            if (c is Transform) return "TRANSFORM_USES_SET_TRANSFORM";
            if (SceneObjects.Role(c) != SceneObjects.None) return "PREFAB_BOUNDARY";
            string path = p.propertyPath;
            string problem = PropertyRules.PathProblem(path);
            if (problem != null) return problem;
            if (!Listed(c, path, visible)) return "HIDDEN";
            foreach (var ancestor in PropertyRules.Ancestors(path))
            {
                var a = so.FindProperty(ancestor);
                if (a == null || a.propertyType != SerializedPropertyType.Generic || a.isArray || !visible.Contains(ancestor)) return "INSIDE_UNSUPPORTED";
            }
            if (!p.editable) return "NOT_EDITABLE";
            if (PropertyRules.KindOf(p.propertyType.ToString(), p.type) == null) return "TYPE_UNSUPPORTED";
            return null;
        }

        public static Dictionary<string, object> List(Component c, string prefix, int page)
        {
            var so = new SerializedObject(c);
            var visible = VisiblePaths(so);
            var all = new List<object>();
            var it = so.GetIterator();
            bool enter = true;
            int walked = 0;
            while (it.Next(enter))
            {
                if (++walked > MaxWalk) throw new Refusal("AUTHORING_LIMIT", "the component has more than " + MaxWalk + " serialized properties");
                string path = it.propertyPath;
                bool listed = Listed(c, path, visible);
                enter = listed && it.propertyType == SerializedPropertyType.Generic && !it.isArray && it.hasChildren;
                if (!listed || (prefix != null && !path.StartsWith(prefix, StringComparison.Ordinal))) continue;
                string kind = PropertyRules.KindOf(it.propertyType.ToString(), it.type);
                string refusal = WriteRefusal(c, so, it, visible);
                var entry = new Dictionary<string, object> {
                    { "path", path }, { "display_name", SceneObjects.Clip(it.displayName, ListedString) },
                    { "property_type", it.propertyType.ToString() }, { "type_name", SceneObjects.Clip(it.type, ListedString) },
                    { "depth", it.depth }, { "kind", kind }, { "writable", refusal == null }, { "refusal", refusal } };
                if (kind != null) Describe(entry, it, kind, c);
                all.Add(entry);
            }
            int start = page * PageSize;
            return new Dictionary<string, object> {
                { "component", SceneObjects.Ref(c) }, { "component_token", SceneObjects.ComponentToken(c) },
                { "count", all.Count }, { "page", page }, { "page_size", PageSize },
                { "next_page", start + PageSize < all.Count ? (object)(page + 1) : null },
                { "properties", all.Skip(start).Take(PageSize).ToList() } };
        }

        static void Describe(Dictionary<string, object> entry, SerializedProperty p, string kind, Component owner)
        {
            object value = Read(p, kind, owner);
            var s = value as string;
            if (kind == "string" && s != null && s.Length > ListedString)
            {
                value = s.Substring(0, ListedString);
                entry["value_truncated"] = true;
            }
            entry["value"] = value;
            if (kind == "enum")
            {
                var names = p.enumNames;
                entry["enum_names"] = names.Take(MaxEnumNames).Cast<object>().ToList();
                entry["enum_names_truncated"] = names.Length > MaxEnumNames;
            }
            if (kind == "object")
            {
                entry["reference"] = ReferenceKind(p.objectReferenceValue, owner);
                entry["accepts"] = PropertyRules.PPtrType(p.type);
            }
        }

        static string ReferenceKind(UnityEngine.Object target, Component owner)
        {
            if (target == null) return "NONE";
            var go = SceneObjects.GameObjectOf(target);
            if (go == null || EditorUtility.IsPersistent(target)) return "ASSET";
            return go.scene == owner.gameObject.scene ? "SCENE" : "OTHER_SCENE";
        }

        // The canonical value of a supported property.
        public static object Read(SerializedProperty p, string kind, Component owner)
        {
            switch (kind)
            {
                case "bool": return p.boolValue;
                case "int8": case "int16": case "int32": case "uint8": case "uint16": return p.longValue;
                case "uint32": return (long)p.uintValue;
                case "int64": return p.longValue.ToString(System.Globalization.CultureInfo.InvariantCulture);
                case "uint64": return p.ulongValue.ToString(System.Globalization.CultureInfo.InvariantCulture);
                case "float32": return (double)p.floatValue;
                case "float64": return p.doubleValue;
                case "string": return p.stringValue;
                case "enum":
                {
                    int i = p.enumValueIndex;
                    var names = p.enumNames;
                    return i >= 0 && i < names.Length ? names[i] : null;
                }
                case "vector2": { var v = p.vector2Value; return SceneObjects.Floats(v.x, v.y); }
                case "vector3": { var v = p.vector3Value; return SceneObjects.Floats(v.x, v.y, v.z); }
                case "vector4": { var v = p.vector4Value; return SceneObjects.Floats(v.x, v.y, v.z, v.w); }
                case "vector2int": { var v = p.vector2IntValue; return new List<object> { (long)v.x, (long)v.y }; }
                case "vector3int": { var v = p.vector3IntValue; return new List<object> { (long)v.x, (long)v.y, (long)v.z }; }
                case "rect": { var v = p.rectValue; return SceneObjects.Floats(v.x, v.y, v.width, v.height); }
                case "rectint": { var v = p.rectIntValue; return new List<object> { (long)v.x, (long)v.y, (long)v.width, (long)v.height }; }
                case "bounds":
                {
                    var v = p.boundsValue;
                    return new List<object> { SceneObjects.Floats(v.center.x, v.center.y, v.center.z), SceneObjects.Floats(v.extents.x, v.extents.y, v.extents.z) };
                }
                case "boundsint":
                {
                    var v = p.boundsIntValue;
                    return new List<object> { new List<object> { (long)v.position.x, (long)v.position.y, (long)v.position.z },
                                              new List<object> { (long)v.size.x, (long)v.size.y, (long)v.size.z } };
                }
                case "color": { var v = p.colorValue; return SceneObjects.Floats(v.r, v.g, v.b, v.a); }
                case "quaternion": { var v = p.quaternionValue; return SceneObjects.Floats(v.x, v.y, v.z, v.w); }
                case "layermask": return (long)(uint)p.intValue;
                case "object":
                {
                    var target = p.objectReferenceValue;
                    return ReferenceKind(target, owner) == "SCENE" ? SceneObjects.Id(target) : null;
                }
            }
            return null;
        }

        // Does the component accept this Scene object for a field declared PPtr<T>? Checked by simple type name;
        // what Unity actually stored is verified by reading the field back.
        public static bool Accepts(string declared, UnityEngine.Object target)
        {
            if (declared == null) return false;
            if (target is GameObject) return declared == "GameObject" || declared == "Object";
            var c = target as Component;
            if (c == null) return false;
            if (declared == "Object" || declared == "Component") return true;
            for (var t = c.GetType(); t != null; t = t.BaseType)
                if (t.Name == declared) return true;
            return false;
        }

        public static void Assign(SerializedProperty p, PropertyValue v, int enumIndex, UnityEngine.Object target)
        {
            var f = v.Floats;
            var i = v.Ints;
            switch (v.Kind)
            {
                case "bool": p.boolValue = v.Bool; break;
                case "int8": case "int16": case "int32": case "uint8": case "uint16": p.intValue = (int)v.Long; break;
                case "uint32": p.uintValue = (uint)v.Long; break;
                case "int64": p.longValue = v.Long; break;
                case "uint64": p.ulongValue = v.ULong; break;
                case "float32": p.floatValue = v.Float; break;
                case "float64": p.doubleValue = v.Double; break;
                case "string": p.stringValue = v.String; break;
                case "enum": p.enumValueIndex = enumIndex; break;
                case "vector2": p.vector2Value = new Vector2(f[0], f[1]); break;
                case "vector3": p.vector3Value = new Vector3(f[0], f[1], f[2]); break;
                case "vector4": p.vector4Value = new Vector4(f[0], f[1], f[2], f[3]); break;
                case "vector2int": p.vector2IntValue = new Vector2Int(i[0], i[1]); break;
                case "vector3int": p.vector3IntValue = new Vector3Int(i[0], i[1], i[2]); break;
                case "rect": p.rectValue = new Rect(f[0], f[1], f[2], f[3]); break;
                case "rectint": p.rectIntValue = new RectInt(i[0], i[1], i[2], i[3]); break;
                case "bounds":
                {
                    var b = new Bounds();
                    b.center = new Vector3(f[0], f[1], f[2]);
                    b.extents = new Vector3(f[3], f[4], f[5]);
                    p.boundsValue = b;
                    break;
                }
                case "boundsint": p.boundsIntValue = new BoundsInt(new Vector3Int(i[0], i[1], i[2]), new Vector3Int(i[3], i[4], i[5])); break;
                case "color": p.colorValue = new Color(f[0], f[1], f[2], f[3]); break;
                case "quaternion": p.quaternionValue = new Quaternion(f[0], f[1], f[2], f[3]); break;
                case "layermask": p.intValue = unchecked((int)(uint)v.Long); break;
                case "object": p.objectReferenceValue = target; break;
                default: throw new Refusal("PROPERTY_UNSUPPORTED", "the kind is not writable");
            }
        }

        // Whether the property now holds exactly what was written (quaternions within 1e-6 per component).
        public static bool Holds(SerializedProperty p, PropertyValue v, int enumIndex, UnityEngine.Object target)
        {
            var f = v.Floats;
            var i = v.Ints;
            switch (v.Kind)
            {
                case "bool": return p.boolValue == v.Bool;
                case "int8": case "int16": case "int32": case "uint8": case "uint16": return p.longValue == v.Long;
                case "uint32": return p.uintValue == (uint)v.Long;
                case "int64": return p.longValue == v.Long;
                case "uint64": return p.ulongValue == v.ULong;
                case "float32": return p.floatValue.Equals(v.Float);
                case "float64": return p.doubleValue.Equals(v.Double);
                case "string": return p.stringValue == v.String;
                case "enum": return p.enumValueIndex == enumIndex;
                case "vector2": { var x = p.vector2Value; return x.x.Equals(f[0]) && x.y.Equals(f[1]); }
                case "vector3": { var x = p.vector3Value; return x.x.Equals(f[0]) && x.y.Equals(f[1]) && x.z.Equals(f[2]); }
                case "vector4": { var x = p.vector4Value; return x.x.Equals(f[0]) && x.y.Equals(f[1]) && x.z.Equals(f[2]) && x.w.Equals(f[3]); }
                case "vector2int": { var x = p.vector2IntValue; return x.x == i[0] && x.y == i[1]; }
                case "vector3int": { var x = p.vector3IntValue; return x.x == i[0] && x.y == i[1] && x.z == i[2]; }
                case "rect": { var x = p.rectValue; return x.x.Equals(f[0]) && x.y.Equals(f[1]) && x.width.Equals(f[2]) && x.height.Equals(f[3]); }
                case "rectint": { var x = p.rectIntValue; return x.x == i[0] && x.y == i[1] && x.width == i[2] && x.height == i[3]; }
                case "bounds":
                {
                    var x = p.boundsValue;
                    return x.center.x.Equals(f[0]) && x.center.y.Equals(f[1]) && x.center.z.Equals(f[2]) &&
                           x.extents.x.Equals(f[3]) && x.extents.y.Equals(f[4]) && x.extents.z.Equals(f[5]);
                }
                case "boundsint":
                {
                    var x = p.boundsIntValue;
                    return x.position.x == i[0] && x.position.y == i[1] && x.position.z == i[2] &&
                           x.size.x == i[3] && x.size.y == i[4] && x.size.z == i[5];
                }
                case "color": { var x = p.colorValue; return x.r.Equals(f[0]) && x.g.Equals(f[1]) && x.b.Equals(f[2]) && x.a.Equals(f[3]); }
                case "quaternion":
                {
                    var x = p.quaternionValue;
                    return Math.Abs(x.x - f[0]) <= 1e-6 && Math.Abs(x.y - f[1]) <= 1e-6 && Math.Abs(x.z - f[2]) <= 1e-6 && Math.Abs(x.w - f[3]) <= 1e-6;
                }
                case "layermask": return (uint)p.intValue == (uint)v.Long;
                case "object": return p.objectReferenceValue == target;
            }
            return false;
        }

        // The write refusal and kind of one property path, exactly as enumeration reports them.
        public static SerializedProperty Writable(Component c, SerializedObject so, string path, string kind)
        {
            string problem = PropertyRules.PathProblem(path);
            if (problem != null) throw new Refusal("PROPERTY_UNSUPPORTED", "the property cannot be written (" + problem + ")");
            var p = so.FindProperty(path);
            if (p == null) throw new Refusal("PROPERTY_UNSUPPORTED", "the component has no serialized property with this path");
            var visible = VisiblePaths(so);
            string refusal = WriteRefusal(c, so, p, visible);
            if (refusal == "PREFAB_BOUNDARY") throw new Refusal("PREFAB_BOUNDARY", "the component is part of a prefab instance; GPOS never creates prefab overrides");
            if (refusal != null) throw new Refusal("PROPERTY_UNSUPPORTED", "the property cannot be written (" + refusal + ")");
            string actual = PropertyRules.KindOf(p.propertyType.ToString(), p.type);
            if (actual != kind) throw new Refusal("PROPERTY_UNSUPPORTED", "the property is of kind " + actual + ", not " + kind);
            return p;
        }
    }
}
