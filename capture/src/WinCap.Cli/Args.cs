namespace WinCap.Cli;

/// <summary>Very small "--key value" / "--flag" parser.</summary>
internal sealed class Args
{
    public readonly List<string> Positional = new();
    public readonly Dictionary<string, string> Options = new(StringComparer.OrdinalIgnoreCase);

    public Args(string[] argv)
    {
        for (int i = 0; i < argv.Length; i++)
        {
            string t = argv[i];
            if (t.StartsWith("--", StringComparison.Ordinal))
            {
                string key = t[2..];
                string value = "true";
                int eq = key.IndexOf('=');
                if (eq >= 0) { value = key[(eq + 1)..]; key = key[..eq]; }
                else if (i + 1 < argv.Length && !argv[i + 1].StartsWith("--", StringComparison.Ordinal))
                {
                    value = argv[++i];
                }
                Options[key] = value;
            }
            else Positional.Add(t);
        }
    }

    public bool Has(string key) => Options.ContainsKey(key);
    public string Get(string key, string fallback = null) => Options.TryGetValue(key, out var v) ? v : fallback;
    public bool Flag(string key) => Options.TryGetValue(key, out var v) && !string.Equals(v, "false", StringComparison.OrdinalIgnoreCase);
    public int GetInt(string key, int fallback) => Options.TryGetValue(key, out var v) && int.TryParse(v, out int n) ? n : fallback;
}
