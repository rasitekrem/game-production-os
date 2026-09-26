// GPOS live bridge — Editor phases and the Play Mode transition state machine (Unity-free core).
// A transition that spans a Domain Reload is recorded once, observed until it completes or its deadline passes,
// and never issued twice. SUCCESS means only that the Editor reached the requested Play Mode state.
using System;
using System.Collections.Generic;
using System.Globalization;

namespace Gpos.LiveBridge
{
    internal struct EditorFlags
    {
        public bool Quitting, Reloading, Compiling, Updating, Playing, WillChange, Paused;
    }

    internal sealed class Pending
    {
        public string RequestId, Op;
        public long Issued, Deadline;
        public List<string> Events = new List<string>();

        public string Save()
        {
            return RequestId + "|" + Op + "|" + Issued.ToString(CultureInfo.InvariantCulture) + "|" +
                   Deadline.ToString(CultureInfo.InvariantCulture) + "|" + string.Join(",", Events);
        }

        public static Pending Load(string text)
        {
            if (string.IsNullOrEmpty(text)) return null;
            var parts = text.Split('|');
            if (parts.Length != 5) throw new FormatException("pending transition");
            var p = new Pending { RequestId = parts[0], Op = parts[1], Issued = long.Parse(parts[2], CultureInfo.InvariantCulture),
                                  Deadline = long.Parse(parts[3], CultureInfo.InvariantCulture) };
            if (parts[4].Length > 0) p.Events.AddRange(parts[4].Split(','));
            return p;
        }
    }

    internal static class Transitions
    {
        public const string Edit = "EDIT", Playing = "PLAYING", Paused = "PAUSED";
        public const int NotStartedSeconds = 5;

        public static string Phase(EditorFlags f)
        {
            if (f.Quitting) return "QUITTING";
            if (f.Reloading) return "RELOADING";
            if (f.Compiling) return "COMPILING";
            if (f.Updating) return "UPDATING";
            if (!f.Playing && f.WillChange) return "ENTERING_PLAYMODE";
            if (f.Playing && !f.WillChange) return "EXITING_PLAYMODE";
            if (f.Playing) return f.Paused ? Paused : Playing;
            return Edit;
        }

        // Why an Editor-state command must not start now (EDITOR_BUSY), or null.
        public static string Busy(string phase, bool pending)
        {
            if (pending) return "PENDING_OPERATION";
            return phase == Edit || phase == Playing || phase == Paused ? null : phase;
        }

        // The state-precondition refusal for an Editor-state command, or null.
        public static string Precondition(string command, string phase)
        {
            switch (command)
            {
                case "enter-playmode": return phase == Edit ? null : "ALREADY_PLAYING";
                case "exit-playmode": return phase == Playing || phase == Paused ? null : "NOT_PLAYING";
                case "pause": return phase == Playing ? null : phase == Paused ? "ALREADY_PAUSED" : "NOT_PLAYING";
                case "resume": return phase == Paused ? null : phase == Playing ? "NOT_PAUSED" : "NOT_PLAYING";
            }
            throw new ArgumentException("not a Play Mode command: " + command);
        }

        // (status, code) once the observed transition is finished, or null while it is still in progress.
        public static string[] Evaluate(Pending p, bool playing, bool willChange, long now)
        {
            if (p.Op == "enter")
            {
                if (playing && willChange && p.Events.Contains("EnteredPlayMode")) return new[] { "OK", null };
                if (p.Events.Contains("EnteredEditMode")) return new[] { "FAILED", "PLAYMODE_ENTER_ABORTED" };
                if (!p.Events.Contains("ExitingEditMode") && !willChange && !playing &&
                    now - p.Issued > TimeSpan.FromSeconds(NotStartedSeconds).Ticks)
                    return new[] { "FAILED", "PLAYMODE_ENTER_NOT_STARTED" };
            }
            else if (p.Op == "exit")
            {
                if (!playing && !willChange && p.Events.Contains("EnteredEditMode")) return new[] { "OK", null };
            }
            if (now > p.Deadline) return new[] { "FAILED", "TRANSITION_TIMEOUT" };
            return null;
        }
    }
}
