// TEST_ONLY Windows stand-in for an external tool executable (GPOS alpha.24).
//
// tests/windows_standin.py compiles this file with the .NET Framework C# compiler into a temporary test fixture and
// copies the result as <name>.exe (git.exe, ffmpeg.exe, adb.exe, Blender.exe ...). It is never part of production:
// the alpha.23 process boundary starts only .exe images, so a test that needs a tool to misbehave on demand needs a
// real .exe; this one runs the Python script that the test wrote beside it.
//
// Fixed locations, verified before anything runs (otherwise exit 97, nothing started):
//   <dir>\<stem>.standin.py      the script, in the same directory as this executable
//   <dir>\standin.interpreter    one line: the absolute, local path of the Python interpreter (.exe) to run it with
// No shell is involved and no command line is composed from configuration: the child is
//   <interpreter> -X utf8 -B <script> <the exact command-line tail this executable received>
// so the script sees exactly the argument vector its caller passed. Its stdout and stderr bytes are relayed
// unchanged and its exit code is returned. The child is created normally, so it stays inside whatever Job Object
// contains this process: a caller's timeout ends both.

using System;
using System.Diagnostics;
using System.IO;
using System.Threading;

static class StandIn
{
    const int Refused = 97;

    static int Refuse(string why)
    {
        Console.Error.WriteLine("gpos-standin: " + why);
        return Refused;
    }

    // The raw command line after argv[0], unchanged (quoted per the C runtime rules by the caller).
    static string Tail(string line)
    {
        int i = 0;
        while (i < line.Length && (line[i] == ' ' || line[i] == '\t')) i++;
        if (i < line.Length && line[i] == '"')
        {
            i++;
            while (i < line.Length && line[i] != '"') i++;
            if (i < line.Length) i++;
        }
        else
        {
            while (i < line.Length && line[i] != ' ' && line[i] != '\t') i++;
        }
        while (i < line.Length && (line[i] == ' ' || line[i] == '\t')) i++;
        return line.Substring(i);
    }

    // One argument quoted for the C runtime parser (the rules subprocess.list2cmdline implements).
    static string Quote(string arg)
    {
        if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return arg;
        var sb = new System.Text.StringBuilder("\"");
        int slashes = 0;
        foreach (char c in arg)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { sb.Append('\\', slashes * 2 + 1); sb.Append('"'); }
            else { sb.Append('\\', slashes); sb.Append(c); }
            slashes = 0;
        }
        sb.Append('\\', slashes * 2);
        sb.Append('"');
        return sb.ToString();
    }

    static void Relay(Stream from, Stream to)
    {
        var buffer = new byte[65536];
        int n;
        while ((n = from.Read(buffer, 0, buffer.Length)) > 0)
        {
            to.Write(buffer, 0, n);
            to.Flush();
        }
    }

    static bool LocalAbsolute(string path)
    {
        return path.Length > 3 && char.IsLetter(path[0]) && path[1] == ':' && path[2] == '\\' && path.IndexOf("..") < 0;
    }

    static int Main()
    {
        string exe = System.Reflection.Assembly.GetEntryAssembly().Location;
        string dir = Path.GetDirectoryName(exe);
        string script = Path.Combine(dir, Path.GetFileNameWithoutExtension(exe) + ".standin.py");
        string config = Path.Combine(dir, "standin.interpreter");
        if (!LocalAbsolute(exe)) return Refuse("the stand-in is not at a local absolute path");
        if (!File.Exists(script)) return Refuse("no script beside the stand-in: " + script);
        if (!File.Exists(config)) return Refuse("no standin.interpreter beside the stand-in");
        string python = File.ReadAllText(config).Trim();
        if (!LocalAbsolute(python) || !python.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) || !File.Exists(python))
            return Refuse("the interpreter is not an existing local .exe: " + python);

        string tail = Tail(Environment.CommandLine);
        var info = new ProcessStartInfo(python, "-X utf8 -B " + Quote(script) + (tail.Length > 0 ? " " + tail : ""));
        info.UseShellExecute = false;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        info.RedirectStandardInput = false;
        info.CreateNoWindow = true;
        using (Process child = Process.Start(info))
        {
            var output = new Thread(() => Relay(child.StandardOutput.BaseStream, Console.OpenStandardOutput()));
            var error = new Thread(() => Relay(child.StandardError.BaseStream, Console.OpenStandardError()));
            output.Start();
            error.Start();
            child.WaitForExit();
            output.Join();
            error.Join();
            return child.ExitCode;
        }
    }
}
