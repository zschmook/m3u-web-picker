from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app_config
import dvr


class DvrTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db_path = self.root / "app.db"
        self.config_path = self.root / "config.json"
        self.recordings_path = self.root / "recordings"
        self.host_path = "C:/Media/M3U DVR"
        self.environment = patch.dict(
            os.environ,
            {
                "M3U_DVR_CONTAINER_DIR": str(self.recordings_path),
                "M3U_DVR_HOST_DIR": self.host_path,
            },
        )
        self.environment.start()
        self.config_patch = patch.object(app_config, "CONFIG_PATH", self.config_path)
        self.config_patch.start()
        dvr.init_db(self.db_path)

    def tearDown(self):
        self.config_patch.stop()
        self.environment.stop()
        self.temp.cleanup()

    def enable_dvr(self):
        return dvr.save_settings({"enabled": True, "host_path": self.host_path})

    def test_commercial_learning_tables_are_initialized(self):
        conn = dvr.connect_database(self.db_path)
        try:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        finally:
            conn.close()

        self.assertIn("dvr_commercial_samples", tables)
        self.assertIn("dvr_commercial_fingerprints", tables)
        self.assertIn("dvr_commercial_comparisons", tables)
        self.assertIn("dvr_commercial_lab_runs", tables)

    def test_transcode_prefers_nvenc_and_keeps_cpu_fallback(self):
        current = dvr.settings()
        with (
            patch.object(dvr, "ffmpeg_executable", return_value="ffmpeg"),
            patch.object(dvr, "_preferred_hevc_encoder", return_value="hevc_nvenc"),
        ):
            gpu = dvr._transcode_command(Path("input.ts"), Path("output.mkv"), current)
            cpu = dvr._transcode_command(
                Path("input.ts"), Path("output.mkv"), current, force_cpu=True
            )

        self.assertIn("hevc_nvenc", gpu)
        self.assertEqual(gpu[gpu.index("-b:v") + 1], "3000k")
        self.assertEqual(gpu[gpu.index("-maxrate") + 1], "4500k")
        self.assertEqual(gpu[gpu.index("-bufsize") + 1], "6000k")
        self.assertIn("libx265", cpu)
        self.assertNotIn("hevc_nvenc", cpu)

    def test_dvr_is_disabled_by_default_and_requires_enablement(self):
        self.assertFalse(dvr.settings()["enabled"])
        self.assertEqual(dvr.settings()["processing_policy"], "scheduled")
        with self.assertRaisesRegex(ValueError, "Enable DVR"):
            dvr.require_ready()

    def test_processing_policy_is_saved_and_invalid_values_are_rejected(self):
        saved = dvr.save_settings({"processing_policy": "immediate"})

        self.assertEqual(saved["processing_policy"], "immediate")
        self.assertEqual(dvr.settings()["processing_policy"], "immediate")
        with self.assertRaisesRegex(ValueError, "processing schedule"):
            dvr.save_settings({"processing_policy": "whenever"})

    def test_immediate_processing_only_starts_for_immediate_policy(self):
        with patch.object(dvr, "_start_maintenance", return_value=True) as start:
            self.assertFalse(dvr.start_immediate_maintenance(self.db_path))
            start.assert_not_called()

            dvr.save_settings({"processing_policy": "immediate"})
            self.assertTrue(dvr.start_immediate_maintenance(self.db_path))
            start.assert_called_once_with(
                self.db_path,
                rerun_if_running=True,
                thread_name="dvr-immediate-maintenance",
            )

    def test_completed_capture_queues_immediate_processing(self):
        self.enable_dvr()
        dvr.save_settings({"processing_policy": "immediate"})
        item = dvr.schedule_recording(
            self.db_path,
            play_url="/guide/play/manual/channel-key",
            tvg_id="station-1",
            channel_name="Station 1",
            title="Nightly News",
            start_at="2035-08-30T20:00:00-04:00",
            stop_at="2035-08-30T21:00:00-04:00",
        )
        capture = self.recordings_path / ".Nightly News.capture.ts"
        final = self.recordings_path / "Nightly News.mkv"
        log = self.recordings_path / ".Nightly News.ffmpeg.log"
        self.recordings_path.mkdir(parents=True, exist_ok=True)
        capture.write_bytes(b"x" * 2048)
        log_handle = log.open("ab")
        dvr._update_recording(self.db_path, item["id"], status="recording")

        with (
            patch.object(dvr, "_valid_media", return_value=True),
            patch.object(dvr, "start_immediate_maintenance", return_value=True) as start,
        ):
            dvr._finish_capture(
                self.db_path,
                item["id"],
                SimpleNamespace(wait=lambda: 0),
                capture,
                final,
                log,
                log_handle,
            )

        start.assert_called_once_with(self.db_path)
        refreshed = dvr.list_recordings(self.db_path)[0]
        self.assertEqual(refreshed["status"], "completed")
        self.assertEqual(refreshed["conversion_status"], "pending")
        self.assertTrue((self.recordings_path / "Nightly News.ts").is_file())

    def test_immediate_worker_rechecks_queue_when_another_capture_finishes(self):
        calls = []

        def maintenance(_db_path):
            calls.append(True)
            if len(calls) == 1:
                with dvr._MAINTENANCE_LOCK:
                    dvr._MAINTENANCE["rerun"] = True
            return {"checked": 1, "converted": 1, "failed": 0}

        with dvr._MAINTENANCE_LOCK:
            dvr._MAINTENANCE.update({"running": True, "rerun": False})
        try:
            with patch.object(dvr, "nightly_maintenance", side_effect=maintenance):
                dvr._maintenance_worker(self.db_path)
            self.assertEqual(len(calls), 2)
            self.assertFalse(dvr.maintenance_state()["running"])
            self.assertEqual(dvr.maintenance_state()["result"]["converted"], 2)
        finally:
            with dvr._MAINTENANCE_LOCK:
                dvr._MAINTENANCE.update({"running": False, "rerun": False})

    def test_enabling_requires_the_exact_mounted_host_path(self):
        with self.assertRaisesRegex(ValueError, "does not match M3U_DVR_DIR"):
            dvr.save_settings({"enabled": True, "host_path": "D:/Wrong Folder"})

        saved = self.enable_dvr()

        self.assertTrue(saved["enabled"])
        self.assertTrue(self.recordings_path.is_dir())
        self.assertTrue(dvr.validate_host_path(self.host_path)["ok"])

    def test_plex_folder_must_live_inside_the_mounted_dvr_folder(self):
        self.enable_dvr()
        with self.assertRaisesRegex(ValueError, "must be inside"):
            dvr.save_settings({"plex_path": "D:/Plex/TV"})

        saved = dvr.save_settings({"plex_path": f"{self.host_path}/PLEX"})

        self.assertEqual(saved["plex_path"], f"{self.host_path}/PLEX")
        self.assertEqual(dvr.plex_dir(), self.recordings_path / "PLEX")
        self.assertTrue((self.recordings_path / "PLEX").is_dir())

    def test_one_time_recordings_are_deduplicated(self):
        self.enable_dvr()
        values = {
            "play_url": "/guide/play/manual/channel-key",
            "tvg_id": "station-1",
            "channel_name": "Station 1",
            "title": "Dateline",
            "start_at": "2035-08-30T20:00:00-04:00",
            "stop_at": "2035-08-30T21:00:00-04:00",
        }

        first = dvr.schedule_recording(self.db_path, **values)
        second = dvr.schedule_recording(self.db_path, **values)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(dvr.list_recordings(self.db_path)), 1)

    def test_processed_commercial_lab_capture_is_hidden_but_kept_in_database(self):
        lab = dvr.schedule_recording(
            self.db_path,
            play_url="/guide/play/manual/lab-channel",
            tvg_id="lab-station",
            channel_name="Lab Station",
            title="Commercial Lab · Lab Station · sample",
            start_at="2035-08-30T20:00:00-04:00",
            stop_at="2035-08-30T20:20:00-04:00",
        )
        dvr._update_recording(
            self.db_path,
            lab["id"],
            status="analyzed",
            output_name="",
            commercial_status="analyzed",
        )

        self.assertEqual(dvr.state(self.db_path)["recordings"], [])
        stored = dvr.list_recordings(self.db_path)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["id"], lab["id"])

    def test_restart_discards_partial_lab_capture_but_keeps_normal_failure(self):
        lab = dvr.schedule_recording(
            self.db_path,
            play_url="/guide/play/manual/lab-channel",
            tvg_id="lab-station",
            channel_name="Lab Station",
            title="Commercial Lab · Lab Station · sample",
            start_at="2035-08-30T20:00:00-04:00",
            stop_at="2035-08-30T20:20:00-04:00",
        )
        normal = dvr.schedule_recording(
            self.db_path,
            play_url="/guide/play/manual/normal-channel",
            tvg_id="normal-station",
            channel_name="Normal Station",
            title="Normal Recording",
            start_at="2035-08-30T21:00:00-04:00",
            stop_at="2035-08-30T22:00:00-04:00",
        )
        lab_file = self.recordings_path / "lab.ts"
        normal_file = self.recordings_path / "normal.ts"
        self.recordings_path.mkdir(parents=True, exist_ok=True)
        lab_file.write_bytes(b"lab")
        normal_file.write_bytes(b"normal")
        dvr._update_recording(self.db_path, lab["id"], status="recording", output_name=lab_file.name)
        dvr._update_recording(self.db_path, normal["id"], status="recording", output_name=normal_file.name)

        self.assertEqual(dvr.recover_interrupted(self.db_path), 2)

        stored = {item["id"]: item for item in dvr.list_recordings(self.db_path)}
        self.assertEqual(stored[lab["id"]]["status"], "discarded")
        self.assertEqual(stored[lab["id"]]["output_name"], "")
        self.assertEqual(stored[normal["id"]]["status"], "failed")
        self.assertFalse(lab_file.exists())
        self.assertTrue(normal_file.exists())
        visible_ids = {item["id"] for item in dvr.state(self.db_path)["recordings"]}
        self.assertNotIn(lab["id"], visible_ids)
        self.assertIn(normal["id"], visible_ids)

    def test_existing_restart_failure_can_be_discarded_without_touching_active_lab(self):
        failed = dvr.schedule_recording(
            self.db_path,
            play_url="/guide/play/manual/failed-lab",
            tvg_id="failed-lab",
            channel_name="Failed Lab",
            title="Commercial Lab · Failed Lab · sample",
            start_at="2035-08-30T20:00:00-04:00",
            stop_at="2035-08-30T20:20:00-04:00",
        )
        active = dvr.schedule_recording(
            self.db_path,
            play_url="/guide/play/manual/active-lab",
            tvg_id="active-lab",
            channel_name="Active Lab",
            title="Commercial Lab · Active Lab · sample",
            start_at="2035-08-30T21:00:00-04:00",
            stop_at="2035-08-30T21:20:00-04:00",
        )
        failed_file = self.recordings_path / "failed.ts"
        active_file = self.recordings_path / "active.ts"
        self.recordings_path.mkdir(parents=True, exist_ok=True)
        failed_file.write_bytes(b"failed")
        active_file.write_bytes(b"active")
        dvr._update_recording(
            self.db_path,
            failed["id"],
            status="failed",
            output_name=failed_file.name,
            error=dvr.INTERRUPTED_ERROR,
        )
        dvr._update_recording(self.db_path, active["id"], status="recording", output_name=active_file.name)

        self.assertEqual(dvr.discard_interrupted_lab_failures(self.db_path), 1)

        stored = {item["id"]: item for item in dvr.list_recordings(self.db_path)}
        self.assertEqual(stored[failed["id"]]["status"], "discarded")
        self.assertEqual(stored[active["id"]]["status"], "recording")
        self.assertFalse(failed_file.exists())
        self.assertTrue(active_file.exists())

    def test_series_rule_schedules_matching_title_on_the_same_channel(self):
        self.enable_dvr()
        epg_path = self.root / "epg.xml"
        epg_path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="station-1"><display-name>Station 1</display-name></channel>
  <programme start="20350830200000 -0400" stop="20350830210000 -0400" channel="station-1"><title>Dateline</title></programme>
  <programme start="20350906200000 -0400" stop="20350906210000 -0400" channel="station-1"><title>Dateline</title></programme>
  <programme start="20350906210000 -0400" stop="20350906220000 -0400" channel="station-1"><title>Other Show</title></programme>
</tv>
""",
            encoding="utf-8",
        )
        rule = dvr.create_series_rule(
            self.db_path,
            title="Dateline",
            tvg_id="station-1",
            channel_name="Station 1",
        )

        created = dvr.sync_series_rules(
            self.db_path,
            channels=[{
                "name": "Station 1",
                "tvg_id": "station-1",
                "play_url": "/guide/play/manual/channel-key",
            }],
            epg_path=epg_path,
            timezone_name="America/New_York",
        )

        recordings = dvr.list_recordings(self.db_path)
        self.assertEqual(created, 2)
        self.assertEqual(len(recordings), 2)
        self.assertTrue(all(item["rule_id"] == rule["id"] for item in recordings))
        self.assertEqual(
            dvr.sync_series_rules(
                self.db_path,
                channels=[{
                    "name": "Station 1",
                    "tvg_id": "station-1",
                    "play_url": "/guide/play/manual/channel-key",
                }],
                epg_path=epg_path,
                timezone_name="America/New_York",
            ),
            0,
        )

    def test_nightly_maintenance_checks_and_converts_idle_ts_file(self):
        self.enable_dvr()
        dvr.save_settings({"remove_commercials": False})
        item = dvr.schedule_recording(
            self.db_path,
            play_url="/guide/play/manual/channel-key",
            tvg_id="station-1",
            channel_name="Station 1",
            title="Nightly News",
            start_at="2035-08-30T20:00:00-04:00",
            stop_at="2035-08-30T21:00:00-04:00",
        )
        source = self.recordings_path / "Nightly News.ts"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"x" * 2048)
        dvr._update_recording(
            self.db_path,
            item["id"],
            status="completed",
            output_name=source.name,
            conversion_status="pending",
        )

        def complete_conversion(command, **kwargs):
            Path(command[-1]).write_bytes(b"m" * 2048)
            return SimpleNamespace(returncode=0)

        dvr.begin_playback(item["id"])
        try:
            with patch.object(dvr, "_idle_recording_file", return_value=True) as playing_idle_check:
                playing_result = dvr.nightly_maintenance(self.db_path)
            self.assertEqual(playing_result["skipped"], 1)
            playing_idle_check.assert_not_called()
        finally:
            dvr.end_playback(item["id"])

        with (
            patch.object(dvr, "_idle_recording_file", return_value=True) as idle_check,
            patch.object(dvr, "_valid_media", return_value=True),
            patch.object(dvr, "_transcode_command", side_effect=lambda source_path, destination, current, **kwargs: ["ffmpeg", str(source_path), str(destination)]),
            patch.object(dvr.subprocess, "run", side_effect=complete_conversion),
        ):
            result = dvr.nightly_maintenance(self.db_path)

        refreshed = dvr.list_recordings(self.db_path)[0]
        idle_check.assert_called_once_with(source)
        self.assertEqual(result["converted"], 1)
        self.assertFalse(source.exists())
        converted = self.recordings_path / "converted" / "Nightly News.mkv"
        self.assertTrue(converted.exists())
        self.assertEqual(refreshed["conversion_status"], "completed")
        self.assertEqual(refreshed["output_name"], "converted/Nightly News.mkv")

    def test_comskip_edl_is_validated_before_commercials_are_removed(self):
        edl = self.root / "recording.edl"
        edl.write_text(
            "60.0 120.0 0\n119.9 150.0 0\ninvalid line\n300.0 301.0 0\n",
            encoding="utf-8",
        )

        cuts = dvr._validated_commercial_plan(edl, 600.0)

        self.assertEqual(cuts, [(60.0, 150.0)])
        self.assertEqual(dvr._kept_intervals(600.0, cuts), [(0.0, 60.0), (150.0, 600.0)])

    def test_successful_conversion_moves_episode_into_plex_library(self):
        self.enable_dvr()
        dvr.save_settings({"remove_commercials": False, "plex_path": f"{self.host_path}/PLEX"})
        item = dvr.schedule_recording(
            self.db_path,
            play_url="/guide/play/manual/channel-key",
            tvg_id="station-1",
            channel_name="Station 1",
            title="The Wall",
            description="S06 E10 Cydney and Jordan",
            start_at="2035-08-30T20:00:00-04:00",
            stop_at="2035-08-30T21:00:00-04:00",
        )
        source = self.recordings_path / "The Wall.ts"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"x" * 2048)
        dvr._update_recording(
            self.db_path,
            item["id"],
            status="completed",
            output_name=source.name,
            conversion_status="pending",
        )

        def complete_conversion(command, **kwargs):
            Path(command[-1]).write_bytes(b"m" * 2048)
            return SimpleNamespace(returncode=0)

        with (
            patch.object(dvr, "_idle_recording_file", return_value=True),
            patch.object(dvr, "_valid_media", return_value=True),
            patch.object(dvr, "_transcode_command", side_effect=lambda source_path, destination, current, **kwargs: ["ffmpeg", str(source_path), str(destination)]),
            patch.object(dvr.subprocess, "run", side_effect=complete_conversion),
        ):
            result = dvr.nightly_maintenance(self.db_path)

        refreshed = dvr.list_recordings(self.db_path)[0]
        destination = self.recordings_path / "PLEX" / "The Wall" / "Season 06" / "The Wall.S06E10.mkv"
        self.assertEqual(result["converted"], 1)
        self.assertEqual(result["moved"], 1)
        self.assertTrue(destination.exists())
        self.assertFalse(source.exists())
        self.assertEqual(refreshed["output_name"], "PLEX/The Wall/Season 06/The Wall.S06E10.mkv")

    def test_nightly_conversion_removes_commercials_into_converted_folder(self):
        self.enable_dvr()
        item = dvr.schedule_recording(
            self.db_path,
            play_url="/guide/play/manual/channel-key",
            tvg_id="station-1",
            channel_name="Station 1",
            title="Nightly News",
            start_at="2035-08-30T20:00:00-04:00",
            stop_at="2035-08-30T21:00:00-04:00",
        )
        source = self.recordings_path / "Nightly News.ts"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"x" * 2048)
        dvr._update_recording(
            self.db_path,
            item["id"],
            status="completed",
            output_name=source.name,
            conversion_status="pending",
            commercial_status="pending",
        )

        def complete_conversion(command, **kwargs):
            Path(command[-1]).write_bytes(b"m" * 2048)
            return SimpleNamespace(returncode=0)

        with (
            patch.object(dvr, "_idle_recording_file", return_value=True),
            patch.object(dvr, "_valid_media", return_value=True),
            patch.object(dvr, "_media_details", return_value=(3600.0, 1)),
            patch.object(dvr, "_detect_commercials", return_value=[(600.0, 720.0), (1200.0, 1320.0)]),
            patch.object(dvr, "_commercial_transcode_command", side_effect=lambda source_path, destination, current, **kwargs: ["ffmpeg", str(source_path), str(destination)]) as cut_command,
            patch.object(dvr.subprocess, "run", side_effect=complete_conversion),
        ):
            result = dvr.nightly_maintenance(self.db_path)

        refreshed = dvr.list_recordings(self.db_path)[0]
        self.assertEqual(result["converted"], 1)
        self.assertEqual(result["commercials_removed"], 2)
        self.assertTrue(cut_command.called)
        self.assertEqual(refreshed["commercial_status"], "removed")
        self.assertEqual(refreshed["commercial_count"], 2)
        self.assertEqual(refreshed["commercial_seconds"], 240.0)
        self.assertEqual(refreshed["output_name"], "converted/Nightly News.mkv")

    def test_failed_commercial_cut_falls_back_to_an_uncut_conversion(self):
        self.enable_dvr()
        item = dvr.schedule_recording(
            self.db_path,
            play_url="/guide/play/manual/channel-key",
            tvg_id="station-1",
            channel_name="Station 1",
            title="Local News",
            start_at="2035-08-30T20:00:00-04:00",
            stop_at="2035-08-30T21:00:00-04:00",
        )
        source = self.recordings_path / "Local News.ts"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"x" * 2048)
        dvr._update_recording(
            self.db_path,
            item["id"],
            status="completed",
            output_name=source.name,
            conversion_status="pending",
        )
        calls = []

        def conversion_result(command, **kwargs):
            calls.append(command[0])
            if command[0] == "cut-ffmpeg":
                return SimpleNamespace(returncode=1)
            Path(command[-1]).write_bytes(b"m" * 2048)
            return SimpleNamespace(returncode=0)

        with (
            patch.object(dvr, "_idle_recording_file", return_value=True),
            patch.object(dvr, "_valid_media", return_value=True),
            patch.object(dvr, "_media_details", return_value=(3600.0, 1)),
            patch.object(dvr, "_detect_commercials", return_value=[(600.0, 720.0)]),
            patch.object(dvr, "_commercial_transcode_command", side_effect=lambda *args, **kwargs: ["cut-ffmpeg", str(source), str(args[1])]),
            patch.object(dvr, "_transcode_command", side_effect=lambda source_path, destination, current, **kwargs: ["plain-ffmpeg", str(source_path), str(destination)]),
            patch.object(dvr.subprocess, "run", side_effect=conversion_result),
        ):
            result = dvr.nightly_maintenance(self.db_path)

        refreshed = dvr.list_recordings(self.db_path)[0]
        self.assertEqual(calls, ["cut-ffmpeg", "plain-ffmpeg"])
        self.assertEqual(result["converted"], 1)
        self.assertEqual(result["commercials_removed"], 0)
        self.assertEqual(refreshed["commercial_status"], "failed")
        self.assertIn("converted without cuts", refreshed["commercial_error"])
        self.assertFalse(source.exists())


class DvrContractTests(unittest.TestCase):
    def test_dvr_settings_and_guide_controls_are_wired(self):
        root = Path(__file__).resolve().parents[1]
        sidebar = (root / "static" / "js" / "ui_sidebar.js").read_text(encoding="utf-8")
        guide = (root / "templates" / "guide.html").read_text(encoding="utf-8")
        compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
        sidebar_template = (root / "templates" / "_modern_sidebar.html").read_text(encoding="utf-8")
        guide_script = (root / "static" / "js" / "guide_dvr.js").read_text(encoding="utf-8")
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        comskip_config = root / "resources" / "comskip.ini"

        self.assertIn('data-settings-panel="dvr"', sidebar)
        self.assertIn('id="uiDvrEnabled"', sidebar)
        self.assertIn('id="uiDvrPath"', sidebar)
        self.assertIn('id="uiDvrPlexPath"', sidebar)
        self.assertIn('id="uiDvrRemoveCommercials"', sidebar)
        self.assertIn('id="uiDvrProcessingPolicy"', sidebar)
        self.assertIn('value="immediate"', sidebar)
        self.assertIn('value="scheduled"', sidebar)
        self.assertIn('value="manual"', sidebar)
        self.assertIn('id="guideRecordOnce"', guide)
        self.assertIn('id="guideRecordSeries"', guide)
        self.assertIn('id="guideDvrQueue"', guide)
        self.assertIn('id="guideDvrSeriesDetails"', guide)
        self.assertIn('id="guideDvrSeriesCount"', guide)
        self.assertIn('id="guideDvrDayNav"', guide)
        self.assertIn('id="guideDvrShowAll"', guide)
        self.assertNotIn('id="guideDvrProcessBtn"', guide)
        self.assertIn("M3U_DVR_DIR", compose)
        self.assertIn("target: /recordings", compose)
        self.assertIn("DVR Recordings", sidebar_template)
        self.assertIn("?dvr=1", sidebar_template)
        self.assertIn('get("dvr") === "1"', guide_script)
        self.assertIn("const badgeCount = active.length;", guide_script)
        self.assertIn('label: "Processing"', guide_script)
        self.assertIn("Next showing:", guide_script)
        self.assertIn("Cancel series", guide_script)
        self.assertIn("selectedGuideWindow", guide_script)
        self.assertIn('["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]', guide_script)
        self.assertIn("selectedDvrWeekday", guide_script)
        self.assertIn("data-dvr-weekday", guide_script)
        self.assertIn("data-dvr-select-series", guide_script)
        self.assertNotIn('mutate("/api/dvr/maintenance"', guide_script)
        self.assertIn('@app.post("/api/dvr/maintenance")', (root / "api" / "dvr.py").read_text(encoding="utf-8"))
        self.assertIn("ffmpeg comskip", dockerfile)
        self.assertTrue(comskip_config.is_file())
        self.assertIn("output_edl=1", comskip_config.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
