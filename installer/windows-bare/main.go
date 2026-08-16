package main

import (
	"archive/zip"
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const (
	appName          = "M3U Web Picker"
	installFolder    = "M3U-Web-Picker"
	sourceRef        = "agent/windows-bare-python"
	sourceArchiveURL = "https://codeload.github.com/zschmook/m3u-web-picker/zip/refs/heads/agent/windows-bare-python"
	webURL           = "http://localhost:9999"
	pythonPackageID  = "Python.Python.3.12"
	ffmpegVersion    = "8.1.2"
	ffmpegURL        = "https://github.com/GyanD/codexffmpeg/releases/download/8.1.2/ffmpeg-8.1.2-full_build.zip"
	ffmpegSHA256     = "b8cdefab5f50590a076c27c2b56b0294a0e6154faded28ba1ba05ebc4f801f57"
)

type paths struct {
	Root       string
	App        string
	Venv       string
	Data       string
	Backups    string
	Cast       string
	FFmpegDir  string
	FFmpegExe  string
	HostEnv    string
	Installed  string
	HostLog    string
	SourceZip  string
	StagingApp string
}

func main() {
	if runtime.GOOS != "windows" {
		fatalf("This installer is intended for Windows.")
	}
	p, err := installationPaths()
	if err != nil {
		fatalf("Could not determine installation paths: %v", err)
	}

	mode := "install"
	if len(os.Args) > 1 {
		mode = strings.ToLower(strings.TrimSpace(os.Args[1]))
	}

	switch mode {
	case "--run":
		if err := runHost(p); err != nil {
			appendLog(p.HostLog, "launcher error: "+err.Error())
			os.Exit(1)
		}
		return
	case "--update":
		banner("M3U Web Picker Update")
		if err := updateInstall(p); err != nil {
			fatalf("Update failed: %v", err)
		}
		fmt.Println("Update complete.")
		_ = openURL(webURL)
		pause()
		return
	case "--uninstall":
		banner("M3U Web Picker Uninstaller")
		if err := uninstall(p); err != nil {
			fatalf("Uninstall failed: %v", err)
		}
		return
	case "--install", "install":
		if err := install(p); err != nil {
			fatalf("Install failed: %v", err)
		}
		return
	default:
		fatalf("Unknown option: %s", mode)
	}
}

func banner(title string) {
	fmt.Println(title)
	fmt.Println(strings.Repeat("=", len(title)))
	fmt.Println()
}

func installationPaths() (paths, error) {
	base := strings.TrimSpace(os.Getenv("LOCALAPPDATA"))
	if base == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return paths{}, err
		}
		base = filepath.Join(home, "AppData", "Local")
	}
	root := filepath.Join(base, installFolder)
	return paths{
		Root:       root,
		App:        filepath.Join(root, "app"),
		Venv:       filepath.Join(root, "venv"),
		Data:       filepath.Join(root, "data"),
		Backups:    filepath.Join(root, "backups"),
		Cast:       filepath.Join(root, "cast-hls"),
		FFmpegDir:  filepath.Join(root, "ffmpeg"),
		FFmpegExe:  filepath.Join(root, "ffmpeg", "ffmpeg.exe"),
		HostEnv:    filepath.Join(root, "host.env"),
		Installed:  filepath.Join(root, "M3U-Web-Picker.exe"),
		HostLog:    filepath.Join(root, "host.log"),
		SourceZip:  filepath.Join(os.TempDir(), "m3u-web-picker-source.zip"),
		StagingApp: filepath.Join(root, ".app-staging"),
	}, nil
}

func install(p paths) error {
	banner("M3U Web Picker Bare Windows Installer")
	fmt.Println("No Docker. No WSL. The app will run directly on Windows Python.")
	fmt.Println()

	if err := os.MkdirAll(p.Root, 0o755); err != nil {
		return err
	}
	for _, dir := range []string{p.Data, p.Backups, p.Cast} {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return err
		}
	}

	python, err := ensurePython312()
	if err != nil {
		return err
	}
	fmt.Printf("Python: %s\n", python)

	if err := ensureFFmpeg(p); err != nil {
		return err
	}
	fmt.Printf("FFmpeg: %s\n", p.FFmpegExe)

	if err := installSource(p); err != nil {
		return err
	}
	if err := prepareVenv(p, python); err != nil {
		return err
	}
	if err := writeHostEnv(p); err != nil {
		return err
	}
	if err := copySelf(p.Installed); err != nil {
		return err
	}
	if err := installShellIntegration(p); err != nil {
		return err
	}

	stopHost(p)
	if err := launchInstalled(p); err != nil {
		return err
	}
	if !waitForApp(45 * time.Second) {
		return errors.New("the Python host did not become reachable on port 9999; check host.log")
	}

	fmt.Println()
	fmt.Println("M3U Web Picker is running at " + webURL)
	fmt.Println("Installed under: " + p.Root)
	fmt.Println("Opening the setup wizard...")
	_ = openURL(webURL)
	fmt.Println()
	fmt.Println("Setup complete.")
	pause()
	return nil
}

func updateInstall(p paths) error {
	if !fileExists(p.Installed) {
		return errors.New("M3U Web Picker is not installed")
	}
	python := filepath.Join(p.Venv, "Scripts", "python.exe")
	if !fileExists(python) {
		return errors.New("managed Python environment is missing; rerun the installer")
	}

	stopHost(p)
	if err := installSource(p); err != nil {
		return err
	}
	if err := installRequirements(p, python); err != nil {
		return err
	}
	if err := launchInstalled(p); err != nil {
		return err
	}
	if !waitForApp(45 * time.Second) {
		return errors.New("updated host did not become reachable; check host.log")
	}
	return nil
}

func ensurePython312() (string, error) {
	if found := findPython312(); found != "" {
		return found, nil
	}

	winget, err := exec.LookPath("winget.exe")
	if err != nil {
		return "", errors.New("Python 3.12 is not installed and winget.exe is unavailable")
	}

	fmt.Println("Python 3.12 was not found. Installing it for the current user...")
	args := []string{
		"install", "-e", "--id", pythonPackageID,
		"--scope", "user", "--silent",
		"--accept-source-agreements", "--accept-package-agreements",
	}
	if err := runStreaming("", winget, args...); err != nil {
		return "", fmt.Errorf("winget could not install Python 3.12: %w", err)
	}

	if found := findPython312(); found != "" {
		return found, nil
	}
	return "", errors.New("Python installation completed but python.exe could not be located; sign out/restart Windows and rerun the installer")
}

func findPython312() string {
	candidates := []string{
		filepath.Join(os.Getenv("LOCALAPPDATA"), "Programs", "Python", "Python312", "python.exe"),
		filepath.Join(os.Getenv("ProgramFiles"), "Python312", "python.exe"),
		filepath.Join(os.Getenv("ProgramW6432"), "Python312", "python.exe"),
	}
	for _, candidate := range candidates {
		if candidate != "" && fileExists(candidate) && pythonIs312(candidate) {
			return candidate
		}
	}

	if py, err := exec.LookPath("py.exe"); err == nil {
		cmd := exec.Command(py, "-3.12", "-c", "import sys; print(sys.executable)")
		out, err := cmd.Output()
		if err == nil {
			candidate := strings.TrimSpace(string(out))
			if fileExists(candidate) && pythonIs312(candidate) {
				return candidate
			}
		}
	}
	if python, err := exec.LookPath("python.exe"); err == nil && pythonIs312(python) {
		return python
	}
	return ""
}

func pythonIs312(python string) bool {
	cmd := exec.Command(python, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
	out, err := cmd.Output()
	return err == nil && strings.TrimSpace(string(out)) == "3.12"
}

func ensureFFmpeg(p paths) error {
	if fileExists(p.FFmpegExe) {
		return nil
	}
	fmt.Printf("Downloading private FFmpeg %s (one-time download)...\n", ffmpegVersion)
	tmp := filepath.Join(os.TempDir(), "m3u-web-picker-ffmpeg.zip")
	if err := downloadFile(ffmpegURL, tmp, 15*time.Minute); err != nil {
		return err
	}
	defer os.Remove(tmp)
	if err := verifySHA256(tmp, ffmpegSHA256); err != nil {
		return err
	}
	if err := os.RemoveAll(p.FFmpegDir); err != nil {
		return err
	}
	if err := os.MkdirAll(p.FFmpegDir, 0o755); err != nil {
		return err
	}
	return extractNamedFile(tmp, "ffmpeg.exe", p.FFmpegExe)
}

func installSource(p paths) error {
	fmt.Printf("Downloading M3U Web Picker source (%s)...\n", sourceRef)
	if err := downloadFile(sourceArchiveURL, p.SourceZip, 5*time.Minute); err != nil {
		return err
	}
	defer os.Remove(p.SourceZip)
	_ = os.RemoveAll(p.StagingApp)
	if err := os.MkdirAll(p.StagingApp, 0o755); err != nil {
		return err
	}
	if err := extractGitHubArchive(p.SourceZip, p.StagingApp); err != nil {
		return err
	}
	for _, required := range []string{"app.py", "host_runtime.py", "requirements.txt"} {
		if !fileExists(filepath.Join(p.StagingApp, required)) {
			return fmt.Errorf("downloaded source is missing %s", required)
		}
	}

	old := p.App + ".old"
	_ = os.RemoveAll(old)
	if dirExists(p.App) {
		if err := os.Rename(p.App, old); err != nil {
			return err
		}
	}
	if err := os.Rename(p.StagingApp, p.App); err != nil {
		if dirExists(old) {
			_ = os.Rename(old, p.App)
		}
		return err
	}
	_ = os.RemoveAll(old)
	return nil
}

func prepareVenv(p paths, basePython string) error {
	python := filepath.Join(p.Venv, "Scripts", "python.exe")
	if !fileExists(python) {
		fmt.Println("Creating private Python environment...")
		_ = os.RemoveAll(p.Venv)
		if err := runStreaming("", basePython, "-m", "venv", p.Venv); err != nil {
			return fmt.Errorf("could not create virtual environment: %w", err)
		}
	}
	return installRequirements(p, python)
}

func installRequirements(p paths, python string) error {
	fmt.Println("Installing Python dependencies...")
	if err := runStreaming(p.App, python, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip"); err != nil {
		return err
	}
	if err := runStreaming(p.App, python, "-m", "pip", "install", "--disable-pip-version-check", "-r", filepath.Join(p.App, "requirements.txt")); err != nil {
		return err
	}
	return nil
}

func writeHostEnv(p paths) error {
	lan := detectLANIPv4()
	if lan != "" {
		fmt.Println("LAN address: " + lan)
	} else {
		fmt.Println("LAN address could not be detected; Cast/Roku LAN relay can be configured later.")
	}
	lines := []string{
		"# Managed by the M3U Web Picker bare Windows installer",
		"PYTHONUNBUFFERED=1",
		"M3U_ONBOARDING_ENABLED=true",
		"M3U_BACKUP_ENABLED=true",
		"M3U_DATA_DIR=" + p.Data,
		"M3U_CAST_HLS_DIR=" + p.Cast,
		"M3U_BACKUP_CONTAINER_DIR=" + p.Backups,
		"M3U_FFMPEG=" + p.FFmpegExe,
		"M3U_PORT=9999",
		"M3U_EXTERNAL_PORT=9999",
		"M3U_LAN_HOST=" + lan,
		"BACKUP_RETENTION_DAYS=30",
		"MASTER_REFRESH_HOUR=3",
		"MASTER_REFRESH_MINUTE=0",
		"",
	}
	return os.WriteFile(p.HostEnv, []byte(strings.Join(lines, "\r\n")), 0o644)
}

func copySelf(destination string) error {
	current, err := os.Executable()
	if err != nil {
		return err
	}
	current, _ = filepath.EvalSymlinks(current)
	if samePath(current, destination) {
		return nil
	}
	src, err := os.Open(current)
	if err != nil {
		return err
	}
	defer src.Close()
	dst, err := os.Create(destination)
	if err != nil {
		return err
	}
	if _, err := io.Copy(dst, src); err != nil {
		dst.Close()
		return err
	}
	return dst.Close()
}

func installShellIntegration(p paths) error {
	runValue := fmt.Sprintf("\"%s\" --run", p.Installed)
	if err := runSilent("", "reg.exe", "add", `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, "/v", appName, "/t", "REG_SZ", "/d", runValue, "/f"); err != nil {
		return fmt.Errorf("could not register startup launcher: %w", err)
	}

	desktop := filepath.Join(os.Getenv("USERPROFILE"), "Desktop")
	if dirExists(desktop) {
		body := "[InternetShortcut]\r\nURL=" + webURL + "\r\n"
		_ = os.WriteFile(filepath.Join(desktop, "M3U Web Picker.url"), []byte(body), 0o644)
	}

	programs := filepath.Join(os.Getenv("APPDATA"), "Microsoft", "Windows", "Start Menu", "Programs", appName)
	if err := os.MkdirAll(programs, 0o755); err != nil {
		return err
	}
	openCmd := "@echo off\r\nstart \"\" \"" + webURL + "\"\r\n"
	updateCmd := "@echo off\r\n\"" + p.Installed + "\" --update\r\n"
	uninstallCmd := "@echo off\r\n\"" + p.Installed + "\" --uninstall\r\n"
	if err := os.WriteFile(filepath.Join(programs, "Open M3U Web Picker.cmd"), []byte(openCmd), 0o644); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(programs, "Update M3U Web Picker.cmd"), []byte(updateCmd), 0o644); err != nil {
		return err
	}
	if err := os.WriteFile(filepath.Join(programs, "Uninstall M3U Web Picker.cmd"), []byte(uninstallCmd), 0o644); err != nil {
		return err
	}
	return nil
}

func launchInstalled(p paths) error {
	if !fileExists(p.Installed) {
		return errors.New("installed launcher is missing")
	}
	cmd := exec.Command(p.Installed, "--run")
	cmd.Dir = p.Root
	if err := cmd.Start(); err != nil {
		return err
	}
	return cmd.Process.Release()
}

func runHost(p paths) error {
	if isAppUp() {
		return nil
	}
	pythonw := filepath.Join(p.Venv, "Scripts", "pythonw.exe")
	if !fileExists(pythonw) {
		return errors.New("managed pythonw.exe is missing")
	}
	runtimeScript := filepath.Join(p.App, "host_runtime.py")
	if !fileExists(runtimeScript) {
		return errors.New("host_runtime.py is missing")
	}

	env, err := mergedEnv(p.HostEnv)
	if err != nil {
		return err
	}
	env = append(env, "M3U_HOST_ENV="+p.HostEnv)

	log, err := os.OpenFile(p.HostLog, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	cmd := exec.Command(pythonw, runtimeScript)
	cmd.Dir = p.App
	cmd.Env = env
	cmd.Stdout = log
	cmd.Stderr = log
	if err := cmd.Start(); err != nil {
		log.Close()
		return err
	}
	_ = log.Close()
	return cmd.Process.Release()
}

func mergedEnv(envFile string) ([]string, error) {
	envMap := map[string]string{}
	for _, item := range os.Environ() {
		if i := strings.IndexByte(item, '='); i > 0 {
			envMap[strings.ToUpper(item[:i])] = item
		}
	}
	data, err := os.ReadFile(envFile)
	if err != nil {
		return nil, err
	}
	for _, raw := range strings.Split(string(data), "\n") {
		line := strings.TrimSpace(strings.TrimSuffix(raw, "\r"))
		if line == "" || strings.HasPrefix(line, "#") || !strings.Contains(line, "=") {
			continue
		}
		key, value, _ := strings.Cut(line, "=")
		key = strings.TrimSpace(key)
		if key != "" {
			envMap[strings.ToUpper(key)] = key + "=" + value
		}
	}
	result := make([]string, 0, len(envMap))
	for _, item := range envMap {
		result = append(result, item)
	}
	return result, nil
}

func stopHost(p paths) {
	pidPath := filepath.Join(p.Data, "host.pid")
	data, err := os.ReadFile(pidPath)
	if err != nil {
		return
	}
	pid := strings.TrimSpace(string(data))
	if _, err := strconv.Atoi(pid); err != nil {
		_ = os.Remove(pidPath)
		return
	}
	_ = runSilent("", "taskkill.exe", "/PID", pid, "/T", "/F")
	_ = os.Remove(pidPath)
	for i := 0; i < 20 && isAppUp(); i++ {
		time.Sleep(250 * time.Millisecond)
	}
}

func uninstall(p paths) error {
	fmt.Println("This removes the host application, private Python environment, and private FFmpeg.")
	keepData := askYesNo("Keep your M3U Web Picker database/config/backups? [Y/n]: ", true)

	stopHost(p)
	_ = runSilent("", "reg.exe", "delete", `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, "/v", appName, "/f")
	desktopLink := filepath.Join(os.Getenv("USERPROFILE"), "Desktop", "M3U Web Picker.url")
	_ = os.Remove(desktopLink)
	programs := filepath.Join(os.Getenv("APPDATA"), "Microsoft", "Windows", "Start Menu", "Programs", appName)
	_ = os.RemoveAll(programs)

	tempScript := filepath.Join(os.TempDir(), fmt.Sprintf("m3u-web-picker-uninstall-%d.cmd", time.Now().UnixNano()))
	lines := []string{
		"@echo off",
		"timeout /t 2 /nobreak >nul",
		"rmdir /s /q \"" + p.App + "\" 2>nul",
		"rmdir /s /q \"" + p.Venv + "\" 2>nul",
		"rmdir /s /q \"" + p.FFmpegDir + "\" 2>nul",
		"del /q \"" + p.HostEnv + "\" 2>nul",
		"del /q \"" + p.HostLog + "\" 2>nul",
		"del /q \"" + p.Installed + "\" 2>nul",
	}
	if keepData {
		lines = append(lines, "echo M3U Web Picker removed. Data was kept in: "+p.Root)
	} else {
		lines = append(lines, "rmdir /s /q \""+p.Root+"\" 2>nul")
	}
	lines = append(lines, "del /q \"%~f0\" 2>nul", "")
	if err := os.WriteFile(tempScript, []byte(strings.Join(lines, "\r\n")), 0o644); err != nil {
		return err
	}
	cmd := exec.Command("cmd.exe", "/c", "start", "", "/min", "cmd.exe", "/c", tempScript)
	if err := cmd.Start(); err != nil {
		return err
	}
	fmt.Println("Uninstall scheduled. This window can close.")
	return nil
}

func waitForApp(timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if isAppUp() {
			return true
		}
		time.Sleep(500 * time.Millisecond)
	}
	return false
}

func isAppUp() bool {
	client := &http.Client{Timeout: 900 * time.Millisecond}
	resp, err := client.Get(webURL + "/api/ui/status")
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode >= 200 && resp.StatusCode < 500
}

func detectLANIPv4() string {
	interfaces, err := net.Interfaces()
	if err != nil {
		return ""
	}
	type candidate struct {
		score int
		ip    string
	}
	var best candidate
	for _, iface := range interfaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		name := strings.ToLower(iface.Name)
		if containsAny(name, "vethernet", "virtual", "hyper-v", "wsl", "docker", "bluetooth", "loopback", "tunnel", "pseudo") {
			continue
		}
		score := 10
		if containsAny(name, "wi-fi", "wifi", "wlan", "ethernet") {
			score += 20
		}
		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, addr := range addrs {
			ip := ipFromAddr(addr)
			if ip == nil {
				continue
			}
			v4 := ip.To4()
			if v4 == nil || v4.IsLoopback() || v4.IsLinkLocalUnicast() || !isRFC1918(v4) {
				continue
			}
			ipScore := score
			if v4[0] == 192 && v4[1] == 168 {
				ipScore += 6
			} else if v4[0] == 10 {
				ipScore += 4
			}
			if ipScore > best.score {
				best = candidate{score: ipScore, ip: v4.String()}
			}
		}
	}
	return best.ip
}

func containsAny(value string, terms ...string) bool {
	for _, term := range terms {
		if strings.Contains(value, term) {
			return true
		}
	}
	return false
}

func ipFromAddr(addr net.Addr) net.IP {
	switch value := addr.(type) {
	case *net.IPNet:
		return value.IP
	case *net.IPAddr:
		return value.IP
	}
	return nil
}

func isRFC1918(ip net.IP) bool {
	v := ip.To4()
	return v != nil && (v[0] == 10 || (v[0] == 172 && v[1] >= 16 && v[1] <= 31) || (v[0] == 192 && v[1] == 168))
}

func downloadFile(url, destination string, timeout time.Duration) error {
	client := &http.Client{Timeout: timeout}
	resp, err := client.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("download failed: %s", resp.Status)
	}
	out, err := os.Create(destination)
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, resp.Body)
	return err
}

func verifySHA256(path, expected string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return err
	}
	actual := hex.EncodeToString(h.Sum(nil))
	if !strings.EqualFold(actual, expected) {
		return fmt.Errorf("checksum mismatch: got %s", actual)
	}
	return nil
}

func extractNamedFile(zipPath, baseName, destination string) error {
	r, err := zip.OpenReader(zipPath)
	if err != nil {
		return err
	}
	defer r.Close()
	for _, f := range r.File {
		if strings.EqualFold(filepath.Base(f.Name), baseName) && !f.FileInfo().IsDir() {
			src, err := f.Open()
			if err != nil {
				return err
			}
			defer src.Close()
			out, err := os.Create(destination)
			if err != nil {
				return err
			}
			if _, err := io.Copy(out, src); err != nil {
				out.Close()
				return err
			}
			return out.Close()
		}
	}
	return fmt.Errorf("%s was not found in archive", baseName)
}

func extractGitHubArchive(zipPath, destination string) error {
	r, err := zip.OpenReader(zipPath)
	if err != nil {
		return err
	}
	defer r.Close()

	cleanRoot := filepath.Clean(destination) + string(os.PathSeparator)
	for _, f := range r.File {
		name := strings.ReplaceAll(f.Name, "\\", "/")
		parts := strings.SplitN(name, "/", 2)
		if len(parts) < 2 || parts[1] == "" {
			continue
		}
		relative := filepath.FromSlash(parts[1])
		target := filepath.Join(destination, relative)
		cleanTarget := filepath.Clean(target)
		if cleanTarget != filepath.Clean(destination) && !strings.HasPrefix(cleanTarget+string(os.PathSeparator), cleanRoot) {
			return fmt.Errorf("unsafe path in source archive: %s", f.Name)
		}
		if f.FileInfo().IsDir() {
			if err := os.MkdirAll(cleanTarget, 0o755); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(cleanTarget), 0o755); err != nil {
			return err
		}
		src, err := f.Open()
		if err != nil {
			return err
		}
		out, err := os.OpenFile(cleanTarget, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, f.Mode())
		if err != nil {
			src.Close()
			return err
		}
		_, copyErr := io.Copy(out, src)
		outErr := out.Close()
		srcErr := src.Close()
		if copyErr != nil {
			return copyErr
		}
		if outErr != nil {
			return outErr
		}
		if srcErr != nil {
			return srcErr
		}
	}
	return nil
}

func runStreaming(dir, executable string, args ...string) error {
	cmd := exec.Command(executable, args...)
	if dir != "" {
		cmd.Dir = dir
	}
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	return cmd.Run()
}

func runSilent(dir, executable string, args ...string) error {
	cmd := exec.Command(executable, args...)
	if dir != "" {
		cmd.Dir = dir
	}
	cmd.Stdout = io.Discard
	cmd.Stderr = io.Discard
	return cmd.Run()
}

func openURL(url string) error {
	return exec.Command("cmd.exe", "/c", "start", "", url).Start()
}

func appendLog(path, message string) {
	_ = os.MkdirAll(filepath.Dir(path), 0o755)
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = fmt.Fprintf(f, "%s %s\n", time.Now().Format(time.RFC3339), message)
}

func askYesNo(prompt string, defaultYes bool) bool {
	fmt.Print(prompt)
	text, _ := bufio.NewReader(os.Stdin).ReadString('\n')
	text = strings.ToLower(strings.TrimSpace(text))
	if text == "" {
		return defaultYes
	}
	return text == "y" || text == "yes"
}

func pause() {
	fmt.Print("Press Enter to close...")
	_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

func dirExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

func samePath(a, b string) bool {
	aa, _ := filepath.Abs(a)
	bb, _ := filepath.Abs(b)
	return strings.EqualFold(filepath.Clean(aa), filepath.Clean(bb))
}

func fatalf(format string, args ...any) {
	fmt.Println()
	fmt.Printf("ERROR: "+format+"\n", args...)
	fmt.Println()
	pause()
	os.Exit(1)
}

// Keep io/fs linked in builds that use older Go path handling through WalkDir.
var _ fs.FileInfo
