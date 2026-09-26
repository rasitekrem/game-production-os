// GPOS live bridge — the closed component catalog. A component type can be added only when it is in this
// catalog, found by its exact id "<assembly>::<full name>". The catalog is rebuilt from the Editor's own type
// information on every use (so it follows every Domain Reload); there is no lookup by name outside it and no
// type is ever loaded from a caller string.
//
// A type is catalogued when it is a concrete, non-generic, public, non-obsolete Component that the Add Component
// menu does not hide, from a Unity runtime module or a Player (non-Editor, non-test) assembly of the project,
// is neither Transform nor RectTransform, and — for a MonoBehaviour — is the class of exactly one runtime MonoScript.
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;

namespace Gpos.LiveBridge
{
    internal sealed class ComponentCatalog
    {
        public List<CatalogEntry> Entries = new List<CatalogEntry>();
        public Dictionary<string, Type> Types = new Dictionary<string, Type>(StringComparer.Ordinal);
        public string Digest;

        public CatalogEntry Find(string typeId) { return Entries.FirstOrDefault(e => e.TypeId == typeId); }
    }

    internal static class Catalog
    {
        internal static string TypeKey(Type t) { return t.Assembly.GetName().Name + "::" + t.FullName; }

        static bool EngineModule(string assembly)
        {
            return assembly.StartsWith("UnityEngine.", StringComparison.Ordinal) && assembly.EndsWith("Module", StringComparison.Ordinal);
        }

        public static ComponentCatalog Build()
        {
            var player = new HashSet<string>(CompilationPipeline.GetAssemblies(AssembliesType.PlayerWithoutTestAssemblies).Select(a => a.name), StringComparer.Ordinal);
            var scripts = new Dictionary<Type, int>();
            foreach (var m in MonoImporter.GetAllRuntimeMonoScripts())
            {
                if (m == null) continue;
                var k = m.GetClass();
                if (k == null) continue;
                int n;
                scripts[k] = scripts.TryGetValue(k, out n) ? n + 1 : 1;
            }
            var catalog = new ComponentCatalog();
            foreach (var t in TypeCache.GetTypesDerivedFrom<Component>())
            {
                if (t.IsAbstract || t.IsGenericTypeDefinition || t.ContainsGenericParameters || !t.IsVisible) continue;
                if (t.IsDefined(typeof(ObsoleteAttribute), false)) continue;
                if (typeof(Transform).IsAssignableFrom(t)) continue;
                if (t.GetCustomAttributes(typeof(AddComponentMenu), false).OfType<AddComponentMenu>().Any(a => string.IsNullOrEmpty(a.componentMenu))) continue;
                string assembly = t.Assembly.GetName().Name;
                if (!EngineModule(assembly) && !player.Contains(assembly)) continue;
                bool script = typeof(MonoBehaviour).IsAssignableFrom(t);
                int mapped;
                if (script && (!scripts.TryGetValue(t, out mapped) || mapped != 1)) continue;
                var e = new CatalogEntry {
                    TypeId = TypeKey(t), Assembly = assembly, FullName = t.FullName, Name = t.Name, Namespace = t.Namespace ?? "",
                    Kind = script ? "SCRIPT" : "NATIVE",
                    DisallowMultiple = t.IsDefined(typeof(DisallowMultipleComponent), true),
                    RunsInEditMode = t.IsDefined(typeof(ExecuteAlways), true) || t.IsDefined(typeof(ExecuteInEditMode), true) };
                foreach (RequireComponent r in t.GetCustomAttributes(typeof(RequireComponent), true))
                    foreach (var required in new[] { r.m_Type0, r.m_Type1, r.m_Type2 })
                        if (required != null) e.Requires.Add(TypeKey(required));
                e.Requires = e.Requires.Distinct().OrderBy(x => x, StringComparer.Ordinal).ToList();
                if (catalog.Types.ContainsKey(e.TypeId)) continue;
                catalog.Types[e.TypeId] = t;
                catalog.Entries.Add(e);
            }
            catalog.Entries = catalog.Entries.OrderBy(e => e.TypeId, StringComparer.Ordinal).ToList();
            catalog.Digest = CatalogDigest.Of(catalog.Entries);
            return catalog;
        }

        // The types a component's own [RequireComponent] attributes name.
        internal static IEnumerable<Type> Required(Type t)
        {
            foreach (RequireComponent r in t.GetCustomAttributes(typeof(RequireComponent), true))
                foreach (var required in new[] { r.m_Type0, r.m_Type1, r.m_Type2 })
                    if (required != null) yield return required;
        }
    }
}
