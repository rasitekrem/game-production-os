// GPOS build entry (bridge 1.5.0) — the package's one fixed -executeMethod target, for a fresh batch-mode Editor that
// GPOS launched itself. It is independent of the live bridge: no [InitializeOnLoad], no static constructor, no
// callback and no menu; it runs only when Unity calls Run(), and only in batch mode outside an import worker (the
// live Bridge lifecycle stays dormant in batch mode). The request comes from one fixed command-line flag naming
// build-request.json in the GPOS execution workspace; the workspace must be exactly
// <gpos root>/.game/gpos-runtime/tool-output/unity/<request id>. It inspects the existing build configuration, or
// builds exactly it into <workspace>/staging/Player.app after re-checking the configuration token, then writes one
// bounded response and exits the Editor. It never switches a target, activates or edits a Build Profile, assigns a
// build setting, publishes, retries or deletes anything.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEditor.Build.Profile;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace Gpos.LiveBridge.Build
{
    public static class BuildEntry
    {
        const int Answered = 0, RequestUnusable = 64, WorkspaceUntrusted = 65, WorkspaceNotFresh = 66, Internal = 70;

        public static void Run()
        {
            if (AssetDatabase.IsAssetImportWorkerProcess() || !Application.isBatchMode) return;   // never a Human's Editor
            int code = Internal;
            try { code = Execute(); }
            catch (Exception) { code = Internal; }
            EditorApplication.Exit(code);
        }

        static int Execute()
        {
            var args = Environment.GetCommandLineArgs();
            int at = Array.IndexOf(args, BuildRules.RequestFlag);
            if (at < 0 || at + 1 >= args.Length || Array.LastIndexOf(args, BuildRules.RequestFlag) != at) return RequestUnusable;
            string requestPath = args[at + 1];
            if (!Path.IsPathRooted(requestPath) || Path.GetFileName(requestPath) != BuildRules.RequestName ||
                Identity.IsLink(requestPath) || !File.Exists(requestPath) || new FileInfo(requestPath).Length > BuildRules.MaxRequestBytes)
                return RequestUnusable;
            BuildRequest request;
            try { request = BuildRules.ParseRequest(File.ReadAllText(requestPath, new UTF8Encoding(false, true))); }
            catch (Exception) { return RequestUnusable; }

            var place = Identity.Locate();
            if (place == null) return WorkspaceUntrusted;
            string[] chain = { Path.Combine(place.GposRoot, ".game", "gpos-runtime"),
                               Path.Combine(place.GposRoot, ".game", "gpos-runtime", "tool-output"),
                               Path.Combine(place.GposRoot, ".game", "gpos-runtime", "tool-output", "unity"),
                               Path.Combine(place.GposRoot, ".game", "gpos-runtime", "tool-output", "unity", request.RequestId) };
            string workspace = Path.GetDirectoryName(requestPath);
            if (chain.Any(Identity.IsLink) || Identity.RealPath(workspace) != chain[3]) return WorkspaceUntrusted;
            workspace = chain[3];

            string response = Path.Combine(workspace, BuildRules.ResponseName), started = Path.Combine(workspace, BuildRules.StartedName);
            foreach (var name in new[] { BuildRules.ResponseName, BuildRules.StartedName, BuildRules.StagingName, "payload", "build-manifest.json" })
            {
                string path = Path.Combine(workspace, name);
                if (File.Exists(path) || Directory.Exists(path) || Identity.IsLink(path)) return WorkspaceNotFresh;
            }

            var answer = request.Operation == "INSPECT" ? Inspect(place) : Build(place, request, workspace, started);
            answer["schema"] = BuildRules.ResponseSchema;
            answer["operation"] = request.Operation;
            answer["request_id"] = request.RequestId;
            answer["unity_version"] = Application.unityVersion;
            WriteOnce(response, Json.Write(answer));
            return Answered;
        }

        static Dictionary<string, object> Answer(string outcome, List<BuildProblem> problems, Dictionary<string, object> configuration)
        {
            return new Dictionary<string, object> {
                { "outcome", outcome }, { "buildable", problems.Count == 0 },
                { "problems", problems.Select(p => (object)new Dictionary<string, object> { { "rule", p.Rule }, { "message", p.Message } }).ToList() },
                { "configuration", configuration }, { "configuration_token", BuildRules.TokenOf(configuration) },
                { "refusal", null }, { "build", null }, { "post", null } };
        }

        static Dictionary<string, object> Inspect(Place place)
        {
            BuildProfile profile;
            var facts = BuildConfiguration.Read(place.ProjectPath, out profile);
            return Answer("INSPECTED", BuildRules.Assess(facts), BuildRules.Configuration(facts));
        }

        static Dictionary<string, object> Refused(Dictionary<string, object> answer, string rule, string message)
        {
            answer["outcome"] = "REFUSED";
            answer["refusal"] = new Dictionary<string, object> { { "rule", rule }, { "message", BuildRules.Clip(message, BuildRules.MaxProblemChars) } };
            return answer;
        }

        static Dictionary<string, object> Build(Place place, BuildRequest request, string workspace, string started)
        {
            BuildProfile profile;
            var facts = BuildConfiguration.Read(place.ProjectPath, out profile);
            var problems = BuildRules.Assess(facts);
            var configuration = BuildRules.Configuration(facts);
            var answer = Answer("REFUSED", problems, configuration);
            if (problems.Count > 0) return Refused(answer, problems[0].Rule, problems[0].Message);
            if ((string)answer["configuration_token"] != request.ExpectedToken)
                return Refused(answer, "CONFIGURATION_CHANGED", "the build configuration no longer matches the inspected configuration token");

            string output = Path.Combine(workspace, BuildRules.StagingName, BuildRules.PayloadName);
            WriteOnce(started, Json.Write(new Dictionary<string, object> {
                { "schema", BuildRules.StartedSchema }, { "request_id", request.RequestId }, { "build_id", request.BuildId },
                { "utc", DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ") } }));
            BuildReport report = profile != null
                ? BuildPipeline.BuildPlayer(new BuildPlayerWithProfileOptions {
                      buildProfile = profile, locationPathName = output, options = BuildOptions.None })
                : BuildPipeline.BuildPlayer(new BuildPlayerOptions {
                      scenes = facts.Scenes.Select(s => s.Path).ToArray(), locationPathName = output,
                      target = BuildTarget.StandaloneOSX, targetGroup = BuildTargetGroup.Standalone,
                      subtarget = (int)StandaloneBuildSubtarget.Player,
                      options = facts.Development ? BuildOptions.Development : BuildOptions.None });

            BuildProfile after;
            var factsAfter = BuildConfiguration.Read(place.ProjectPath, out after);
            answer["outcome"] = "BUILT";
            answer["build"] = Normalize(report);
            answer["post"] = new Dictionary<string, object> {
                { "configuration_token", BuildRules.TokenOf(BuildRules.Configuration(factsAfter)) },
                { "active_target", factsAfter.ActiveTarget },
                { "profile_path", factsAfter.Profile == null ? null : factsAfter.Profile.Path },
                { "development", factsAfter.Development } };
            return answer;
        }

        // The BuildReport as bounded facts: the summary, and at most MaxMessages error, exception or assert messages.
        static Dictionary<string, object> Normalize(BuildReport report)
        {
            if (report == null) return null;
            var s = report.summary;
            var messages = new List<object>();
            long total = 0;
            foreach (var step in report.steps)
                foreach (var m in step.messages)
                {
                    if (m.type != LogType.Error && m.type != LogType.Exception && m.type != LogType.Assert) continue;
                    total++;
                    if (messages.Count < BuildRules.MaxMessages)
                        messages.Add(new Dictionary<string, object> { { "step", BuildRules.Clip(step.name, BuildRules.MaxStepChars) },
                                                                      { "type", m.type.ToString() },
                                                                      { "text", BuildRules.Clip(m.content, BuildRules.MaxMessageChars) } });
                }
            return new Dictionary<string, object> {
                { "result", s.result.ToString() }, { "guid", s.guid.ToString() }, { "platform", s.platform.ToString() },
                { "output_path", s.outputPath }, { "total_errors", (long)s.totalErrors }, { "total_warnings", (long)s.totalWarnings },
                { "total_size", (long)s.totalSize }, { "duration_ms", (long)s.totalTime.TotalMilliseconds },
                { "development_observed", (s.options & BuildOptions.Development) != 0 },
                { "options_text", BuildRules.Clip(s.options.ToString(), BuildRules.MaxOptionsChars) },
                { "messages", messages }, { "error_message_count", total } };
        }

        // Written once: a temporary file in the same directory, then one rename that never replaces an existing file.
        static void WriteOnce(string path, string text)
        {
            string temporary = path + ".tmp";
            File.WriteAllText(temporary, text, new UTF8Encoding(false));
            File.Move(temporary, path);
        }
    }
}
