package main

import (
	"archive/zip"
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const (
	appName       = "M3U Web Picker"
	repoURL       = "https://github.com/zschmook/m3u-web-picker.git"
	repoBranch    = "main"
	webURL        = "http://localhost:9999"
	minGitVersion = "2.55.0.4"
	minGitURL     = "https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.4/MinGit-2.55.0.4-64-bit.zip"
	minGitSHA256  = "4e03f94c2ffbf70be337e005cee02661c732dbfc81031a078bda9299b9a7d644"
)

func main() {
	if runtime.GOOS != "windows" {
		fatalf("This installer is intended for Windows.")
	}

	fmt.Println("M3U Web Picker Windows Installer")
	fmt.Println("================================")
	fmt.Println()
	fmt.Println("This installer keeps its own private MinGit copy and does not modify your system Git or PATH.")
	fmt.Println()

	installDir, err := installationDirectory()
	if err != nil {
		fatalf("Could not determine the installation directory: %v", err)
	}
	repoDir := filepath.Join(installDir, "repo")
	minGitDir := filepath.Join(installDir, "mingit")

	if err := os.MkdirAll(installDir, 0o755); err != nil {
		fatalf("Could not create %s: %v", installDir, err)
	}

	dockerPath, err := ensureDockerInstalled()
	if err != nil {
		fatalf("Docker Desktop is required: %v", err)
	}
	if err := ensureDockerRunning(dockerPath); err != nil {
		fatalf("Docker Desktop is installed but is not ready: %v", err)
	}

	gitPath, err := ensureMinGit(minGitDir)
	if err != nil {
		fatalf("Could not prepare private MinGit: %v", err)
	}

	if err := syncRepository(gitPath, repoDir); err != nil {
		fatalf("Could not download/update M3U Web Picker: %v", err)
	}

	if err := ensureEnv(repoDir); err != nil {
		fatalf("Could not configure LAN settings: %v", err)
	}

	if err := writeHelpers(installDir, repoDir, gitPath, dockerPath); err != nil {
		fmt.Printf("Warning: could not create helper shortcuts: %v\n", err)
	}

	fmt.Println()
	fmt.Println("Building and starting M3U Web Picker. The first Docker build can take a few minutes...")
	if err := runStreaming(repoDir, dockerPath, "compose", "up", "-d", "--build"); err != nil {
		fatalf("Docker build/start failed: %v", err)
	}

	_ = createDesktopURL(webURL)

	fmt.Println()
	fmt.Println("M3U Web Picker is running at:")
	fmt.Println("  " + webURL)
	fmt.Println()
	fmt.Println("Opening the setup wizard in your browser...")
	_ = openURL(webURL)
	fmt.Println()
	fmt.Printf("Installed files: %s\n", installDir)
	fmt.Println("Setup complete.")
	fmt.Println()
	fmt.Print("Press Enter to close...")
	_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
}

func installationDirectory() (string, error) {
	base := strings.TrimSpace(os.Getenv("LOCALAPPDATA"))
	if base == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		base = filepath.Join(home, "AppData", "Local")
	}
	return filepath.Join(base, "M3U-Web-Picker"), nil
}

func ensureDockerInstalled() (string, error) {
	if path := findDocker(); path != "" {
		fmt.Printf("Docker: %s\n", path)
		return path, nil
	}

	fmt.Println("Docker Desktop was not found.")
	winget, _ := exec.LookPath("winget.exe")
	if winget == "" {
		_ = openURL("https://www.docker.com/products/docker-desktop/")
		return "", errors.New("install Docker Desktop, then run this installer again")
	}

	if !askYesNo("Install Docker Desktop with winget now? [Y/n]: ", true) {
		_ = openURL("https://www.docker.com/products/docker-desktop/")
		return "", errors.New("Docker Desktop installation was skipped")
	}

	if err := runStreaming("", winget, "install", "-e", "--id", "Docker.DockerDesktop", "--accept-source-agreements", "--accept-package-agreements"); err != nil {
		return "", fmt.Errorf("winget could not install Docker Desktop: %w", err)
	}

	if path := findDocker(); path != "" {
		return path, nil
	}
	return "", errors.New("Docker Desktop installation finished, but docker.exe was not found; a sign-out or reboot may be required")
}

func findDocker() string {
	if path, err := exec.LookPath("docker.exe"); err == nil {
		return path
	}
	candidates := []string{
		filepath.Join(os.Getenv("ProgramFiles"), "Docker", "Docker", "resources", "bin", "docker.exe"),
		filepath.Join(os.Getenv("ProgramW6432"), "Docker", "Docker", "resources", "bin", "docker.exe"),
	}
	for _, candidate := range candidates {
		if candidate != "" && fileExists(candidate) {
			return candidate
		}
	}
	return ""
}

func ensureDockerRunning(dockerPath string) error {
	if commandOK("", dockerPath, "info") {
		fmt.Println("Docker daemon: ready")
		return nil
	}

	desktop := filepath.Join(os.Getenv("ProgramFiles"), "Docker", "Docker", "Docker Desktop.exe")
	if fileExists(desktop) {
		fmt.Println("Starting Docker Desktop...")
		_ = exec.Command(desktop).Start()
	} else {
		fmt.Println("Docker daemon is not running. Start Docker Desktop now.")
	}

	deadline := time.Now().Add(3 * time.Minute)
	for time.Now().Before(deadline) {
		if commandOK("", dockerPath, "info") {
			fmt.Println("Docker daemon: ready")
			return nil
		}
		fmt.Print(".")
		time.Sleep(3 * time.Second)
	}
	fmt.Println()
	return errors.New("timed out waiting for Docker Desktop; start Docker Desktop and rerun the installer")
}

func ensureMinGit(minGitDir string) (string, error) {
	gitPath := filepath.Join(minGitDir, "cmd", "git.exe")
	marker := filepath.Join(minGitDir, ".m3u-min-git-version")
	if fileExists(gitPath) {
		if data, err := os.ReadFile(marker); err == nil && strings.TrimSpace(string(data)) == minGitVersion {
			fmt.Printf("MinGit %s: ready\n", minGitVersion)
			return gitPath, nil
		}
	}

	fmt.Printf("Downloading private MinGit %s...\n", minGitVersion)
	tmpZip := filepath.Join(os.TempDir(), "m3u-web-picker-mingit.zip")
	if err := downloadFile(minGitURL, tmpZip); err != nil {
		return "", err
	}
	defer os.Remove(tmpZip)

	if err := verifySHA256(tmpZip, minGitSHA256); err != nil {
		return "", err
	}

	_ = os.RemoveAll(minGitDir)
	if err := os.MkdirAll(minGitDir, 0o755); err != nil {
		return "", err
	}
	if err := unzip(tmpZip, minGitDir); err != nil {
		return "", err
	}
	if !fileExists(gitPath) {
		return "", errors.New("MinGit archive did not contain cmd\\git.exe")
	}
	if err := os.WriteFile(marker, []byte(minGitVersion+"\r\n"), 0o644); err != nil {
		return "", err
	}
	fmt.Println("MinGit: ready")
	return gitPath, nil
}

func syncRepository(gitPath, repoDir string) error {
	gitDir := filepath.Join(repoDir, ".git")
	if !dirExists(gitDir) {
		fmt.Println("Downloading M3U Web Picker main branch...")
		if err := os.MkdirAll(filepath.Dir(repoDir), 0o755); err != nil {
			return err
		}
		return runStreaming("", gitPath, "-c", "http.sslBackend=schannel", "clone", "--branch", repoBranch, "--single-branch", repoURL, repoDir)
	}

	fmt.Println("Refreshing existing M3U Web Picker installation...")
	if err := runStreaming(repoDir, gitPath, "-c", "http.sslBackend=schannel", "fetch", "origin", repoBranch); err != nil {
		return err
	}
	if err := runStreaming(repoDir, gitPath, "checkout", repoBranch); err != nil {
		return err
	}
	return runStreaming(repoDir, gitPath, "reset", "--hard", "origin/"+repoBranch)
}

func ensureEnv(repoDir string) error {
	envPath := filepath.Join(repoDir, ".env")
	data, err := os.ReadFile(envPath)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	text := string(data)
	if strings.Contains(text, "M3U_LAN_HOST=") {
		return nil
	}

	host := detectPrivateIPv4()
	if host == "" {
		fmt.Println("LAN address could not be detected automatically. Cast/Roku can be configured later with M3U_LAN_HOST in .env.")
		return nil
	}
	if text != "" && !strings.HasSuffix(text, "\n") {
		text += "\r\n"
	}
	text += "M3U_LAN_HOST=" + host + "\r\n"
	fmt.Printf("LAN address: %s\n", host)
	return os.WriteFile(envPath, []byte(text), 0o644)
}

func detectPrivateIPv4() string {
	ifaces, err := net.Interfaces()
	if err != nil {
		return ""
	}
	var fallback string
	for _, iface := range ifaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, addr := range addrs {
			var ip net.IP
			switch value := addr.(type) {
			case *net.IPNet:
				ip = value.IP
			case *net.IPAddr:
				ip = value.IP
			}
			ipv4 := ip.To4()
			if ipv4 == nil || ipv4.IsLoopback() || ipv4.IsLinkLocalUnicast() {
				continue
			}
			value := ipv4.String()
			if isRFC1918(ipv4) {
				return value
			}
			if fallback == "" {
				fallback = value
			}
		}
	}
	return fallback
}

func isRFC1918(ip net.IP) bool {
	v := ip.To4()
	if v == nil {
		return false
	}
	return v[0] == 10 || (v[0] == 172 && v[1] >= 16 && v[1] <= 31) || (v[0] == 192 && v[1] == 168)
}

func writeHelpers(installDir, repoDir, gitPath, dockerPath string) error {
	updateCmd := filepath.Join(installDir, "Update M3U Web Picker.cmd")
	updateBody := fmt.Sprintf(`@echo off
setlocal
cd /d "%s"
"%s" -c http.sslBackend=schannel fetch origin main
if errorlevel 1 goto :fail
"%s" checkout main
if errorlevel 1 goto :fail
"%s" reset --hard origin/main
if errorlevel 1 goto :fail
"%s" compose up -d --build
if errorlevel 1 goto :fail
echo.
echo M3U Web Picker updated successfully.
pause
exit /b 0
:fail
echo.
echo Update failed. Review the error above.
pause
exit /b 1
`, repoDir, gitPath, gitPath, gitPath, dockerPath)
	if err := os.WriteFile(updateCmd, []byte(updateBody), 0o644); err != nil {
		return err
	}

	openCmd := filepath.Join(installDir, "Open M3U Web Picker.cmd")
	if err := os.WriteFile(openCmd, []byte("@echo off\r\nstart \"\" \""+webURL+"\"\r\n"), 0o644); err != nil {
		return err
	}
	return nil
}

func createDesktopURL(url string) error {
	desktop := filepath.Join(os.Getenv("USERPROFILE"), "Desktop")
	if desktop == "Desktop" || !dirExists(desktop) {
		return errors.New("desktop folder not found")
	}
	body := "[InternetShortcut]\r\nURL=" + url + "\r\n"
	return os.WriteFile(filepath.Join(desktop, "M3U Web Picker.url"), []byte(body), 0o644)
}

func downloadFile(url, path string) error {
	client := &http.Client{Timeout: 5 * time.Minute}
	resp, err := client.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("download failed: %s", resp.Status)
	}
	out, err := os.Create(path)
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
		return fmt.Errorf("MinGit checksum mismatch: got %s", actual)
	}
	return nil
}

func unzip(source, destination string) error {
	r, err := zip.OpenReader(source)
	if err != nil {
		return err
	}
	defer r.Close()

	cleanDestination := filepath.Clean(destination) + string(os.PathSeparator)
	for _, f := range r.File {
		target := filepath.Join(destination, f.Name)
		if !strings.HasPrefix(filepath.Clean(target)+string(os.PathSeparator), cleanDestination) {
			return fmt.Errorf("unsafe path in MinGit archive: %s", f.Name)
		}
		if f.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0o755); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		src, err := f.Open()
		if err != nil {
			return err
		}
		dst, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, f.Mode())
		if err != nil {
			src.Close()
			return err
		}
		_, copyErr := io.Copy(dst, src)
		closeDstErr := dst.Close()
		closeSrcErr := src.Close()
		if copyErr != nil {
			return copyErr
		}
		if closeDstErr != nil {
			return closeDstErr
		}
		if closeSrcErr != nil {
			return closeSrcErr
		}
	}
	return nil
}

func commandOK(dir, executable string, args ...string) bool {
	cmd := exec.Command(executable, args...)
	if dir != "" {
		cmd.Dir = dir
	}
	cmd.Stdout = io.Discard
	cmd.Stderr = io.Discard
	return cmd.Run() == nil
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

func askYesNo(prompt string, defaultYes bool) bool {
	fmt.Print(prompt)
	text, _ := bufio.NewReader(os.Stdin).ReadString('\n')
	text = strings.ToLower(strings.TrimSpace(text))
	if text == "" {
		return defaultYes
	}
	return text == "y" || text == "yes"
}

func openURL(url string) error {
	return exec.Command("cmd.exe", "/c", "start", "", url).Start()
}

func fileExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

func dirExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.IsDir()
}

func fatalf(format string, args ...any) {
	fmt.Println()
	fmt.Printf("ERROR: "+format+"\n", args...)
	fmt.Println()
	fmt.Print("Press Enter to close...")
	_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
	os.Exit(1)
}
