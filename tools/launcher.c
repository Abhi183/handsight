/*
 * Handsight.app's executable. Runs the Python app inside this process, so macOS sees one
 * app called Handsight (Dock icon, name, camera permission) instead of "Python".
 *
 * Two builds:
 *   default    (tools/make_app.sh)  uses the project's .venv, found next to the .app or at
 *              the path baked in at build time (PROJECT_DIR). Output goes to desk.log in
 *              the project folder.
 *   -DBUNDLED  (tools/build_dmg.sh) uses the Python and code inside the app
 *              (Contents/Resources/python and Contents/Resources/app). Your files and
 *              desk.log go to ~/Library/Application Support/Handsight.
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <dirent.h>
#include <errno.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#if !defined(PROJECT_DIR) && !defined(BUNDLED)
#error "build with -DPROJECT_DIR=\"/path/to/handsight\""
#endif
#ifndef APP_MODULE
#define APP_MODULE "handsight.menubar"
#endif

#ifndef BUNDLED
static int is_project(const char *dir) {
    char path[PATH_MAX];
    struct stat st;
    snprintf(path, sizeof path, "%s/handsight", dir);
    return stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

/* The folder holding Handsight.app if it is a project checkout, else PROJECT_DIR. */
static void find_project(char *out, size_t size) {
    char exe[PATH_MAX], real[PATH_MAX];
    uint32_t len = sizeof exe;
    if (_NSGetExecutablePath(exe, &len) == 0 && realpath(exe, real) != NULL) {
        /* .../X/Handsight.app/Contents/MacOS/Handsight -> .../X */
        char *dir = real;
        for (int i = 0; i < 4; i++) dir = dirname(dir);
        if (is_project(dir)) {
            snprintf(out, size, "%s", dir);
            return;
        }
    }
    snprintf(out, size, "%s", PROJECT_DIR);
}
#endif

static void fail(const char *message) {
    char cmd[1024];
    fprintf(stderr, "Handsight: %s\n", message);
    snprintf(cmd, sizeof cmd,
             "osascript -e 'display alert \"Handsight could not start\" message \"%s\"'",
             message);
    system(cmd);
    exit(1);
}

#ifdef BUNDLED
/* .../Handsight.app/Contents/MacOS/Handsight -> .../Handsight.app/Contents/Resources */
static void find_resources(char *out, size_t size) {
    char exe[PATH_MAX], real[PATH_MAX];
    uint32_t len = sizeof exe;
    if (_NSGetExecutablePath(exe, &len) != 0 || realpath(exe, real) == NULL)
        fail("Could not find where the app is.");
    char *dir = dirname(dirname(real));
    snprintf(out, size, "%s/Resources", dir);
}

static int make_dirs(const char *path) {
    char tmp[PATH_MAX];
    snprintf(tmp, sizeof tmp, "%s", path);
    for (char *p = tmp + 1; *p; p++) {
        if (*p != '/') continue;
        *p = '\0';
        if (mkdir(tmp, 0755) != 0 && errno != EEXIST) return -1;
        *p = '/';
    }
    return (mkdir(tmp, 0755) != 0 && errno != EEXIST) ? -1 : 0;
}

/* Handsight was called Desk Guard: take over its folder (face profile, gestures, settings,
   stats, photos) the first time. If a Handsight folder already exists but holds none of
   that (an earlier test run, a failed first start), the old files are moved in one by one,
   never over a file that is already there. */
static int has_user_data(const char *dir) {
    static const char *keys[] = {"menubar_settings.json", "face_profile.npy", "gestures.json"};
    char path[PATH_MAX];
    struct stat st;
    for (size_t i = 0; i < sizeof keys / sizeof keys[0]; i++) {
        snprintf(path, sizeof path, "%s/%s", dir, keys[i]);
        if (stat(path, &st) == 0) return 1;
    }
    return 0;
}

static void merge_old_data(const char *old, const char *data) {
    DIR *dir = opendir(old);
    struct dirent *entry;
    char from[PATH_MAX], to[PATH_MAX];
    struct stat st;
    if (dir == NULL) return;
    while ((entry = readdir(dir)) != NULL) {
        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) continue;
        snprintf(from, sizeof from, "%s/%s", old, entry->d_name);
        snprintf(to, sizeof to, "%s/%s", data, entry->d_name);
        if (stat(to, &st) != 0 && rename(from, to) != 0)
            fprintf(stderr, "Handsight: could not move %s\n", from);
    }
    closedir(dir);
    rmdir(old);  /* only goes if everything moved */
}

static void move_old_data(const char *home, const char *data) {
    char old[PATH_MAX];
    struct stat st;
    snprintf(old, sizeof old, "%s/Library/Application Support/Desk Guard", home);
    if (stat(old, &st) != 0 || !S_ISDIR(st.st_mode)) return;  /* nothing to take over */
    if (stat(data, &st) != 0) {
        if (rename(old, data) != 0) fprintf(stderr, "Handsight: could not move %s\n", old);
        return;
    }
    if (!has_user_data(data)) merge_old_data(old, data);
}

/* Sets up the bundled Python; `python` gets its executable, `project` the data folder. */
static void setup_bundled(char *python, char *project, size_t size) {
    char res[PATH_MAX], app[PATH_MAX];
    const char *home = getenv("HOME");
    if (home == NULL || *home == '\0') fail("HOME is not set.");
    find_resources(res, sizeof res);
    snprintf(python, size, "%s/python/bin/python3.12", res);
    if (access(python, X_OK) != 0) fail("The app is damaged. Download it again.");
    snprintf(app, sizeof app, "%s/app", res);
    snprintf(project, size, "%s/Library/Application Support/Handsight", home);
    move_old_data(home, project);
    if (make_dirs(project) != 0) fail("Could not create the Handsight folder in Application Support.");
    setenv("PYTHONPATH", app, 1);
    setenv("DESK_GUARD_DATA", project, 1);
    setenv("PYTHONNOUSERSITE", "1", 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);  /* the signed app must stay unchanged */
    char mpl[PATH_MAX];  /* `import mediapipe` loads matplotlib, which wants a cache folder */
    snprintf(mpl, sizeof mpl, "%s/.matplotlib", project);
    setenv("MPLCONFIGDIR", mpl, 1);
    setenv("MPLBACKEND", "Agg", 1);
    unsetenv("PYTHONHOME");
}
#endif

int main(int argc, char **argv) {
    char project[PATH_MAX], python[PATH_MAX], log[PATH_MAX];
    (void)argc;
#ifdef BUNDLED
    setup_bundled(python, project, sizeof project);
#else
    find_project(project, sizeof project);
    snprintf(python, sizeof python, "%s/.venv/bin/python", project);
    if (access(python, X_OK) != 0)
        fail("It is not set up yet. In Terminal, in the handsight folder, run ./run desk once.");
#endif
    if (chdir(project) != 0) fail("Could not open the Handsight folder.");

    /* Send output to desk.log. Each stream on its own: freopen closes the old stream
       first, so a failure must not leave the other one closed too. */
    snprintf(log, sizeof log, "%s/desk.log", project);
    FILE *out = fopen(log, "a");
    if (out != NULL) {
        fclose(out);  /* it can be opened: now point both streams at it */
        if (freopen(log, "a", stdout) != NULL) setvbuf(stdout, NULL, _IOLBF, 0);
        if (freopen(log, "a", stderr) != NULL) setvbuf(stderr, NULL, _IONBF, 0);
    }

    PyStatus status;
    PyConfig config;
    PyConfig_InitPythonConfig(&config);
#ifdef BUNDLED
    config.safe_path = 1;  /* don't put the data folder on sys.path; only the app's code runs */
#endif
    /* Bundled: executable inside Resources/python makes Python find its home there.
       Checkout: pointing executable at the venv's python makes Python find .venv/pyvenv.cfg and use
       the venv's site-packages, exactly as if the venv python had been started. */
    status = PyConfig_SetBytesString(&config, &config.executable, python);
    if (PyStatus_Exception(status)) goto error;
    status = PyConfig_SetBytesString(&config, &config.program_name, python);
    if (PyStatus_Exception(status)) goto error;
    char *args[] = {argv[0], "-u", "-m", APP_MODULE};
    status = PyConfig_SetBytesArgv(&config, 4, args);
    if (PyStatus_Exception(status)) goto error;
    status = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(status)) goto error;
    PyConfig_Clear(&config);
    return Py_RunMain();

error:
    PyConfig_Clear(&config);
    if (PyStatus_IsExit(status)) return status.exitcode;
    Py_ExitStatusException(status);
}
