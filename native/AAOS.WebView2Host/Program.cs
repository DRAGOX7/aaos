using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace AAOS.WebView2Host;

internal sealed record HostInput(
    string LaunchUrl,
    string ProfilePath,
    int TimeoutSeconds,
    bool Diagnostic,
    bool SelfTest);

internal static class Program
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private const string SyntheticLaunchUrl = "data:text/html,<a id='aaos' href='aaos://invalid'>continue</a><script>document.getElementById('aaos').click()</script>";

    // IPC exit contract: 0 means completed with cleanup, 1 is a safe host or
    // cleanup failure, and 2 is invalid input or request validation failure.
    [STAThread]
    private static int Main()
    {
        bool diagnostic = false;
        try
        {
            HostInput? input = ReadInput();
            diagnostic = input?.Diagnostic == true;
            if (input?.SelfTest == true)
            {
                return WriteStatus("HOST_ERROR", 1, null, true, "IPC_SELF_TEST");
            }
            if (input is null || input.TimeoutSeconds <= 0 ||
                !Uri.TryCreate(input.LaunchUrl, UriKind.Absolute, out Uri? launch) ||
                !IsApprovedLaunchUri(input.LaunchUrl, launch))
            {
                return WriteStatus("HOST_ERROR", 2, null, diagnostic, "REQUEST_VALIDATION_FAILED");
            }
            if (!IsApprovedProfilePath(input.ProfilePath))
            {
                return WriteStatus("HOST_ERROR", 2, null, diagnostic, "PROFILE_INITIALIZATION_FAILED");
            }

            ApplicationConfiguration.Initialize();
            using var form = new AuthForm(launch, input.ProfilePath, input.TimeoutSeconds, input.Diagnostic);
            Application.Run(form);
            form.CleanupCompleted.GetAwaiter().GetResult();
            return WriteStatus(form.Result, form.CleanupResult ? 0 : 1, form);
        }
        catch (InvalidDataException)
        {
            return WriteStatus("HOST_ERROR", 2, null, diagnostic, "INPUT_DESERIALIZATION_FAILED");
        }
        catch
        {
            return WriteStatus("HOST_ERROR", 1, null, diagnostic, "UNCLASSIFIED_NATIVE_EXCEPTION");
        }
    }

    private static int WriteStatus(
        string status,
        int exitCode = 0,
        AuthForm? form = null,
        bool diagnostic = false,
        string? diagnosticStage = null)
    {
        if (form?.Diagnostic == true || diagnostic)
        {
            Console.WriteLine($"INTERACTION_STATUS={status}");
            Console.WriteLine($"FAILURE_STAGE={form?.FailureStage ?? diagnosticStage}");
        }
        else
        {
            Console.WriteLine(status);
        }
        Console.Out.Flush();
        Application.ExitThread();
        return exitCode;
    }

    private static bool IsApprovedLaunchUri(string raw, Uri uri)
    {
        return raw == SyntheticLaunchUrl || (uri.Scheme == Uri.UriSchemeHttps &&
            string.Equals(uri.Host, "elearn.just.edu.jo", StringComparison.OrdinalIgnoreCase) &&
            uri.Port is -1 or 443 &&
            uri.AbsolutePath == "/current/admin/tool/mobile/launch.php" &&
            IsApprovedLaunchQuery(uri.Query) &&
            string.IsNullOrEmpty(uri.Fragment) &&
            string.IsNullOrEmpty(uri.UserInfo));
    }

    private static bool IsApprovedLaunchQuery(string query)
    {
        const string prefix = "?service=moodle_mobile_app&passport=";
        const string suffix = "&urlscheme=aaos";
        if (!query.StartsWith(prefix, StringComparison.Ordinal) ||
            !query.EndsWith(suffix, StringComparison.Ordinal))
        {
            return false;
        }

        string passport = query[prefix.Length..^suffix.Length];
        return passport.Length > 0 && !passport.Contains('&');
    }

    private static bool IsApprovedProfilePath(string profilePath)
    {
        try
        {
            string fullPath = Path.GetFullPath(profilePath);
            string tempRoot = Path.TrimEndingDirectorySeparator(Path.GetFullPath(Path.GetTempPath())) +
                Path.DirectorySeparatorChar;
            string leaf = Path.GetFileName(Path.TrimEndingDirectorySeparator(fullPath));
            return fullPath.StartsWith(tempRoot, StringComparison.OrdinalIgnoreCase) &&
                leaf.StartsWith("embedded-", StringComparison.Ordinal) &&
                leaf.Length == 41 &&
                leaf[9..].All(Uri.IsHexDigit);
        }
        catch (Exception error) when (error is ArgumentException or NotSupportedException)
        {
            return false;
        }
    }

    private static HostInput? ReadInput()
    {
        using var reader = new StreamReader(Console.OpenStandardInput());
        char[] buffer = new char[16385];
        int count = reader.ReadBlock(buffer, 0, buffer.Length);
        if (count <= 0 || count == buffer.Length)
        {
            return null;
        }

        string payload = new(buffer, 0, count);
        try
        {
            return JsonSerializer.Deserialize<HostInput>(payload, JsonOptions);
        }
        catch (JsonException)
        {
            throw new InvalidDataException("INPUT_DESERIALIZATION_FAILED");
        }
    }
}

internal sealed class AuthForm : Form
{
    private readonly Uri _launchUrl;
    private readonly string _profilePath;
    private readonly int _timeoutSeconds;
    private readonly WebView2 _webView = new() { Dock = DockStyle.Fill };
    private readonly System.Windows.Forms.Timer _timer = new();
    private readonly TaskCompletionSource<bool> _cleanupCompleted = new(
        TaskCreationOptions.RunContinuationsAsynchronously);
    private TaskCompletionSource<bool>? _browserProcessExited;
    private CoreWebView2Environment? _environment;
    private CoreWebView2? _coreWebView2;
    private uint _browserProcessId;
    private bool _cleanupStarted;
    private bool _cleanupResult;
    private bool _allowClose;
    private readonly bool _diagnostic;
    private string _failureStage = "UNEXPECTED_RUNTIME_FAILURE";
    private bool AAOS_EXTERNAL_SCHEME_EVENT_SEEN;
    private bool _terminal;

    public AuthForm(Uri launchUrl, string profilePath, int timeoutSeconds, bool diagnostic)
    {
        _launchUrl = launchUrl;
        _profilePath = profilePath;
        _timeoutSeconds = timeoutSeconds;
        _diagnostic = diagnostic;
        Text = "AAOS Moodle Authentication";
        Width = 960;
        Height = 720;
        Controls.Add(_webView);
        Shown += OnShown;
        FormClosing += OnFormClosing;
        FormClosed += OnFormClosed;
    }

    public string Result { get; private set; } = "HOST_ERROR";
    public Task CleanupCompleted => _cleanupCompleted.Task;
    public bool CleanupResult => _cleanupResult;
    public bool Diagnostic => _diagnostic;
    public string FailureStage => _failureStage;

    private async void OnShown(object? sender, EventArgs args)
    {
        try
        {
            var options = new CoreWebView2EnvironmentOptions();
            CoreWebView2Environment environment;
            _failureStage = "WEBVIEW2_ENVIRONMENT_CREATION_FAILED";
            try
            {
                environment = await CoreWebView2Environment.CreateAsync(
                    browserExecutableFolder: null,
                    userDataFolder: _profilePath,
                    options: options);
            }
            catch
            {
                _failureStage = "WEBVIEW2_ENVIRONMENT_INIT_FAILED";
                Finish("HOST_ERROR");
                return;
            }
            _environment = environment;
            _browserProcessExited = new TaskCompletionSource<bool>(
                TaskCreationOptions.RunContinuationsAsynchronously);
            environment.BrowserProcessExited += OnBrowserProcessExited;
            try
            {
                _failureStage = "CORE_WEBVIEW2_INITIALIZATION_FAILED";
                await _webView.EnsureCoreWebView2Async(environment);
            }
            catch
            {
                _failureStage = "CORE_WEBVIEW2_INIT_FAILED";
                Finish("HOST_ERROR");
                return;
            }
            CoreWebView2 core = _webView.CoreWebView2;
            _coreWebView2 = core;
            _browserProcessId = core.BrowserProcessId;
            try
            {
                _failureStage = "EVENT_SUBSCRIPTION_FAILED";
                core.Settings.AreDevToolsEnabled = false;
                core.Settings.AreDefaultContextMenusEnabled = false;
                core.Settings.IsPasswordAutosaveEnabled = false;
                core.Settings.IsGeneralAutofillEnabled = false;
                core.LaunchingExternalUriScheme += OnLaunchingExternalUriScheme;
                core.NewWindowRequested += OnNewWindowRequested;
            }
            catch
            {
                Finish("HOST_ERROR");
                return;
            }
            _timer.Interval = checked(_timeoutSeconds * 1000);
            _timer.Tick += (_, _) =>
            {
                _failureStage = "EXTERNAL_SCHEME_EVENT_NOT_SEEN";
                Finish("TIMED_OUT");
            };
            _timer.Start();
            try
            {
                _failureStage = "INITIAL_CONTENT_LOAD_FAILED";
                core.Navigate(_launchUrl.ToString());
            }
            catch
            {
                _failureStage = "INITIAL_NAVIGATION_FAILED";
                Finish("HOST_ERROR");
            }
        }
        catch
        {
            Finish("HOST_ERROR");
        }
    }

    private void OnLaunchingExternalUriScheme(object? sender, CoreWebView2LaunchingExternalUriSchemeEventArgs args)
    {
        // Inspect only the scheme prefix. Never parse, log, persist, or return the URI.
        int separator = args.Uri.IndexOf(':');
        bool isAaos = separator == _AaosScheme.Length &&
            args.Uri.AsSpan(0, separator).Equals(_AaosScheme.AsSpan(), StringComparison.OrdinalIgnoreCase);
        args.Cancel = !isAaos;
        if (isAaos)
        {
            AAOS_EXTERNAL_SCHEME_EVENT_SEEN = true;
            args.Cancel = false;
            if (AAOS_EXTERNAL_SCHEME_EVENT_SEEN)
            {
                Result = "CALLBACK_DISPATCHED";
                _failureStage = "NO_FAILURE";
                _terminal = true;
                BeginInvoke(FinishAfterDispatch);
            }
        }
    }

    private const string _AaosScheme = "aaos";

    private void OnNewWindowRequested(object? sender, CoreWebView2NewWindowRequestedEventArgs args)
    {
        if (!Uri.TryCreate(args.Uri, UriKind.Absolute, out Uri? uri) ||
            uri.Scheme != Uri.UriSchemeHttps ||
            !string.Equals(uri.Host, _launchUrl.Host, StringComparison.OrdinalIgnoreCase) ||
            uri.Port is not (443 or -1))
        {
            args.Handled = true;
            return;
        }

        args.Handled = true;
        _webView.CoreWebView2.Navigate(uri.ToString());
    }

    private void FinishAfterDispatch()
    {
        BeginShutdown();
    }

    private void Finish(string result)
    {
        if (_terminal)
        {
            return;
        }

        _terminal = true;
        Result = result;
        BeginShutdown();
    }

    private async void BeginShutdown()
    {
        if (_cleanupStarted)
        {
            return;
        }

        _cleanupStarted = true;
        _timer.Stop();
        try
        {
            if (_coreWebView2 is not null)
            {
                _coreWebView2.LaunchingExternalUriScheme -= OnLaunchingExternalUriScheme;
                _coreWebView2.NewWindowRequested -= OnNewWindowRequested;
                _coreWebView2 = null;
            }

            _webView.Dispose();
            _cleanupResult = await WaitForBrowserProcessExit();
            if (!_cleanupResult)
            {
                _failureStage = "BROWSER_SHUTDOWN_TIMEOUT";
            }
        }
        catch
        {
            _failureStage = "SHUTDOWN_FAILED";
            _cleanupResult = false;
        }
        finally
        {
            if (_cleanupResult && _environment is not null)
            {
                _environment.BrowserProcessExited -= OnBrowserProcessExited;
                _environment = null;
                _browserProcessExited = null;
            }

            _cleanupCompleted.TrySetResult(true);
            _allowClose = true;
            Close();
        }
    }

    private void OnFormClosing(object? sender, FormClosingEventArgs args)
    {
        if (_allowClose)
        {
            return;
        }

        args.Cancel = true;
        if (_cleanupStarted)
        {
            return;
        }

        if (!_terminal)
        {
            _failureStage = "FORM_CLOSED_BEFORE_RESULT";
            Result = "USER_CANCELLED";
            _terminal = true;
        }

        BeginShutdown();
    }

    private void OnBrowserProcessExited(
        object? sender, CoreWebView2BrowserProcessExitedEventArgs args)
    {
        if (args.BrowserProcessId == _browserProcessId)
        {
            _browserProcessExited?.TrySetResult(true);
        }
    }

    private async Task<bool> WaitForBrowserProcessExit()
    {
        if (_environment is null || _browserProcessExited is null || _browserProcessId == 0)
        {
            return true;
        }

        try
        {
            await _browserProcessExited.Task.WaitAsync(TimeSpan.FromSeconds(5)).ConfigureAwait(false);
            return true;
        }
        catch (TimeoutException)
        {
            return false;
        }
    }

    private void OnFormClosed(object? sender, FormClosedEventArgs args)
    {
        _cleanupCompleted.TrySetResult(true);
    }
}
