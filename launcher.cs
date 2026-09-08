using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

public class fruityrpc_launcher
{
    static string base_dir()
    {
        return AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
    }

    static string find_python(out string prefix_args)
    {
        prefix_args = "";

        string windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        string[] launchers = { "pyw.exe", "py.exe" };
        foreach (string name in launchers)
        {
            string candidate = Path.Combine(windows, name);
            if (File.Exists(candidate))
            {
                prefix_args = "-3 ";
                return candidate;
            }
        }

        string[] on_path = { "pythonw.exe", "python.exe" };
        string path_variable = Environment.GetEnvironmentVariable("PATH") ?? "";
        string alias = null;
        foreach (string name in on_path)
        {
            foreach (string directory in path_variable.Split(';'))
            {
                if (directory.Length == 0) continue;
                string candidate;
                try { candidate = Path.Combine(directory.Trim(), name); }
                catch { continue; }
                if (!File.Exists(candidate)) continue;
                if (candidate.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    if (alias == null) alias = candidate;
                    continue;
                }
                return candidate;
            }
        }
        return alias;
    }

    [STAThread]
    public static int Main(string[] args)
    {
        string root = base_dir();
        string script = Path.Combine(root, "fruityrpc.py");
        if (!File.Exists(script))
        {
            MessageBox.Show("fruityrpc.py was not found next to FruityRPC.exe."
                            + "\nKeep every FruityRPC file in one folder.",
                            "FruityRPC", MessageBoxButtons.OK,
                            MessageBoxIcon.Error);
            return 1;
        }

        string prefix;
        string python = find_python(out prefix);
        if (python == null)
        {
            MessageBox.Show("Python 3 was not found.\n\nInstall it from "
                            + "python.org and tick \"Add python.exe to PATH\","
                            + " then try again.", "FruityRPC",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }

        StringBuilder command_line = new StringBuilder();
        command_line.Append(prefix);
        command_line.Append('"').Append(script).Append('"');
        foreach (string argument in args)
        {
            command_line.Append(" \"").Append(argument).Append('"');
        }

        ProcessStartInfo info = new ProcessStartInfo(python,
                                                     command_line.ToString());
        info.WorkingDirectory = root;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.WindowStyle = ProcessWindowStyle.Hidden;

        try
        {
            Process.Start(info);
        }
        catch (Exception error)
        {
            MessageBox.Show("Could not start FruityRPC:\n" + error.Message,
                            "FruityRPC", MessageBoxButtons.OK,
                            MessageBoxIcon.Error);
            return 1;
        }
        return 0;
    }
}
