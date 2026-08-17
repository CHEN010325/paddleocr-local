import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import controller
import server


class ControllerLeasePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_file = Path(self.temp_dir.name) / "controller-ocr-leases.json"
        self.patchers = [
            patch.object(server, "CONTROLLER_OCR_LEASE_STORE_ENABLED", True),
            patch.object(server, "CONTROLLER_OCR_LEASE_STORE_FILE", self.store_file),
        ]
        for patcher in self.patchers:
            patcher.start()
        server.model_runtime_lock = asyncio.Lock()
        server.model_runtime_task = None
        server.unlimited_ocr_backend_task = None
        server.ocr_active_count = 0
        server.controller_ocr_leases.clear()
        server.controller_ocr_lease_store_loaded = True
        server.controller_ocr_lease_store_error = None
        server.set_model_runtime_operation("idle", "", server.DEFAULT_RUNTIME_MODEL_ID)

    async def asyncTearDown(self):
        server.controller_ocr_leases.clear()
        server.controller_ocr_lease_store_error = None
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()

    async def acquire_ready_lease(self, *, ttl=0, now=100.0):
        snapshot = {
            "runningModelIds": ["target"],
            "readyModelIds": ["target"],
            "exclusivityViolation": False,
        }
        with (
            patch.dict(server.MODEL_RUNTIME_CONFIG, {"target": {}}, clear=True),
            patch.object(
                server,
                "model_runtime_status",
                new=AsyncMock(return_value={"running": True, "ready": True}),
            ),
            patch.object(
                server,
                "runtime_exclusivity_snapshot",
                new=AsyncMock(return_value=snapshot),
            ),
            patch.object(server, "CONTROLLER_OCR_LEASE_TTL_SECONDS", ttl),
            patch.object(server.time, "time", return_value=now),
        ):
            return await server.acquire_controller_ocr_lease("target")

    async def test_restart_restores_permanent_lease_and_original_id_can_release_it(self):
        lease = await self.acquire_ready_lease()
        self.assertIsNone(lease["expiresAt"])
        persisted = json.loads(self.store_file.read_text(encoding="utf-8"))
        self.assertEqual(persisted["version"], server.CONTROLLER_OCR_LEASE_STORE_VERSION)
        self.assertIn(lease["leaseId"], persisted["leases"])

        # Simulate a fresh controller process while the Web process still owns
        # the lease id returned by the previous controller.
        server.controller_ocr_leases.clear()
        server.controller_ocr_lease_store_loaded = False
        server.controller_ocr_lease_store_error = None
        schedule = AsyncMock()
        with (
            patch.object(controller.server, "ensure_task_data_dir"),
            patch.object(controller.server, "model_control_available", return_value=True),
            patch.object(
                controller.server,
                "schedule_model_runtime_activation",
                schedule,
            ),
        ):
            async with controller.lifespan(controller.app):
                pass
        schedule.assert_not_awaited()
        self.assertIn(lease["leaseId"], server.controller_ocr_leases)

        self.assertTrue(await server.release_controller_ocr_lease(lease["leaseId"]))
        self.assertEqual(json.loads(self.store_file.read_text(encoding="utf-8"))["leases"], {})

        # A later controller startup is no longer blocked once the recovered
        # Web-owned lease has been durably released.
        server.controller_ocr_leases.clear()
        server.controller_ocr_lease_store_loaded = False
        schedule.reset_mock()
        with (
            patch.object(controller.server, "ensure_task_data_dir"),
            patch.object(controller.server, "model_control_available", return_value=True),
            patch.object(
                controller.server,
                "schedule_model_runtime_activation",
                schedule,
            ),
        ):
            async with controller.lifespan(controller.app):
                pass
        schedule.assert_awaited_once_with(server.DEFAULT_RUNTIME_MODEL_ID)

    async def test_corrupt_store_is_diagnostic_and_blocks_all_model_operations(self):
        self.store_file.write_text('{"version": 1, "leases": ', encoding="utf-8")
        server.controller_ocr_lease_store_loaded = False
        server.controller_ocr_lease_store_error = None
        schedule = AsyncMock()
        with (
            patch.object(controller.server, "ensure_task_data_dir"),
            patch.object(controller.server, "model_control_available", return_value=True),
            patch.object(
                controller.server,
                "schedule_model_runtime_activation",
                schedule,
            ),
        ):
            async with controller.lifespan(controller.app):
                pass
            health = await controller.health()
        schedule.assert_not_awaited()
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(health["controllerOcrLeaseStore"]["state"], "error")
        self.assertIn("Failed to load", health["controllerOcrLeaseStore"]["error"])

        with (
            patch.dict(server.MODEL_RUNTIME_CONFIG, {}, clear=True),
            patch.dict(server.MODEL_RUNTIME_CONTAINER_GROUPS, {}, clear=True),
            patch.object(server, "model_control_available", return_value=False),
        ):
            runtime = await server.build_model_runtime_payload()
        self.assertEqual(runtime["controllerOcrLeaseStore"]["state"], "error")
        self.assertFalse(runtime["controllerOcrLeaseStore"]["healthy"])

        self.store_file.write_text(
            '{"version":1,"leases":{"held":{"leaseId":"held","modelId":"target",'
            '"createdAt":1,"expiresAt":null}},"leases":{}}',
            encoding="utf-8",
        )
        self.assertFalse(server.load_controller_ocr_leases())
        self.assertIn("duplicate JSON key", server.controller_ocr_lease_store_error)

        with (
            patch.dict(server.MODEL_RUNTIME_CONFIG, {"target": {}}, clear=True),
            patch.object(server, "model_control_available", return_value=True),
        ):
            with self.assertRaises(HTTPException) as switch_error:
                await server.schedule_model_runtime_activation("target")
        self.assertEqual(switch_error.exception.status_code, 503)
        with self.assertRaises(HTTPException) as release_error:
            await server.release_controller_ocr_lease("lease-owned-by-web")
        self.assertEqual(release_error.exception.status_code, 503)

    async def test_acquire_and_release_publish_only_after_atomic_replace(self):
        with patch.object(server.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(HTTPException) as acquire_error:
                await self.acquire_ready_lease()
        self.assertEqual(acquire_error.exception.status_code, 503)
        self.assertEqual(server.controller_ocr_leases, {})
        self.assertIn("Failed to persist", server.controller_ocr_lease_store_error)

        lease = await self.acquire_ready_lease()
        with patch.object(server.os, "replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(HTTPException) as release_error:
                await server.release_controller_ocr_lease(lease["leaseId"])
        self.assertEqual(release_error.exception.status_code, 503)
        self.assertIn(lease["leaseId"], server.controller_ocr_leases)
        self.assertIn(
            lease["leaseId"],
            json.loads(self.store_file.read_text(encoding="utf-8"))["leases"],
        )

        self.assertTrue(await server.release_controller_ocr_lease(lease["leaseId"]))
        self.assertIsNone(server.controller_ocr_lease_store_error)

    async def test_ttl_prune_is_durable_while_default_ttl_remains_permanent(self):
        expiring = await self.acquire_ready_lease(ttl=5, now=100.0)
        self.assertEqual(expiring["expiresAt"], 105.0)
        self.assertTrue(server.prune_controller_ocr_leases(now=106.0))
        self.assertEqual(server.controller_ocr_leases, {})
        self.assertEqual(json.loads(self.store_file.read_text(encoding="utf-8"))["leases"], {})

        permanent = await self.acquire_ready_lease(ttl=0, now=200.0)
        self.assertTrue(server.prune_controller_ocr_leases(now=10_000.0))
        self.assertIn(permanent["leaseId"], server.controller_ocr_leases)

    async def test_store_status_and_lease_schema_validation_matrix(self):
        server.controller_ocr_lease_store_loaded = True
        server.controller_ocr_lease_store_error = None
        self.assertEqual(server.controller_ocr_lease_store_status()["state"], "ready")
        server.controller_ocr_lease_store_error = "previous atomic write failed"
        status = server.controller_ocr_lease_store_status()
        self.assertEqual(status["state"], "error")
        self.assertFalse(status["healthy"])
        server.controller_ocr_lease_store_error = None

        invalid_leases = [
            ("", {}, "lease id"),
            ("lease", [], "must be an object"),
            ("lease", {"leaseId": "other"}, "mismatched"),
            (
                "lease",
                {"leaseId": "lease", "modelId": "", "createdAt": 1, "expiresAt": None},
                "modelId",
            ),
            (
                "lease",
                {"leaseId": "lease", "modelId": "target", "createdAt": True, "expiresAt": None},
                "createdAt",
            ),
            (
                "lease",
                {"leaseId": "lease", "modelId": "target", "createdAt": float("nan"), "expiresAt": None},
                "createdAt",
            ),
            (
                "lease",
                {"leaseId": "lease", "modelId": "target", "createdAt": 1, "expiresAt": False},
                "expiresAt",
            ),
            (
                "lease",
                {"leaseId": "lease", "modelId": "target", "createdAt": 1, "expiresAt": float("inf")},
                "expiresAt",
            ),
            (
                "lease",
                {"leaseId": "lease", "modelId": "target", "createdAt": 5, "expiresAt": 4},
                "expires before",
            ),
        ]
        for lease_id, raw_lease, expected_message in invalid_leases:
            with self.subTest(expected_message=expected_message):
                with self.assertRaisesRegex(ValueError, expected_message):
                    server._validate_controller_ocr_lease(lease_id, raw_lease)

        validated = server._validate_controller_ocr_lease(
            "lease",
            {"leaseId": "lease", "modelId": "target", "createdAt": 1, "expiresAt": 2},
        )
        self.assertEqual(validated["createdAt"], 1.0)
        self.assertEqual(validated["expiresAt"], 2.0)

    async def test_persist_requires_loaded_store_and_tolerates_posix_chmod_failure(self):
        server.controller_ocr_lease_store_loaded = False
        with self.assertRaisesRegex(RuntimeError, "not loaded successfully"):
            server._persist_controller_ocr_leases({})

        server.controller_ocr_lease_store_loaded = True
        with (
            patch.object(server.os, "name", "posix"),
            patch.object(server.os, "chmod", side_effect=OSError("chmod unsupported")) as chmod,
        ):
            server._persist_controller_ocr_leases({})
        chmod.assert_called_once()
        self.assertTrue(self.store_file.exists())
        self.assertIsNone(server.controller_ocr_lease_store_error)

    async def test_load_rejects_invalid_schemas_and_handles_missing_or_unprunable_store(self):
        invalid_stores = [
            ([], "root must be an object"),
            ({"version": True, "leases": {}}, "unsupported"),
            ({"version": 99, "leases": {}}, "unsupported"),
            ({"version": 1, "leases": []}, "leases must be an object"),
        ]
        for payload, expected_message in invalid_stores:
            with self.subTest(expected_message=expected_message):
                self.store_file.write_text(json.dumps(payload), encoding="utf-8")
                server.controller_ocr_lease_store_loaded = False
                server.controller_ocr_lease_store_error = None
                self.assertFalse(server.load_controller_ocr_leases())
                self.assertIn(expected_message, server.controller_ocr_lease_store_error)

        self.store_file.unlink()
        server.controller_ocr_lease_store_loaded = False
        server.controller_ocr_lease_store_error = "old error"
        self.assertTrue(server.load_controller_ocr_leases())
        self.assertEqual(server.controller_ocr_leases, {})
        self.assertIsNone(server.controller_ocr_lease_store_error)

        self.store_file.write_text(
            json.dumps({"version": 1, "leases": {}}),
            encoding="utf-8",
        )
        server.controller_ocr_lease_store_loaded = False
        with patch.object(server, "prune_controller_ocr_leases", return_value=False):
            self.assertFalse(server.load_controller_ocr_leases())
        self.assertTrue(server.controller_ocr_lease_store_loaded)

    async def test_prune_acquire_and_release_cleanup_fail_closed_branches(self):
        server.controller_ocr_leases["expired"] = {
            "leaseId": "expired",
            "modelId": "target",
            "createdAt": 1,
            "expiresAt": 2,
        }
        with (
            patch.object(
                server,
                "_commit_controller_ocr_leases",
                side_effect=OSError("disk unavailable"),
            ),
            patch.object(server.logger, "exception") as logged,
        ):
            self.assertFalse(server.prune_controller_ocr_leases(now=3))
        self.assertIn("expired", server.controller_ocr_leases)
        logged.assert_called_once()

        with patch.dict(server.MODEL_RUNTIME_CONFIG, {"target": {}}, clear=True):
            with self.assertRaises(HTTPException) as unknown_model:
                await server.acquire_controller_ocr_lease("missing")
        self.assertEqual(unknown_model.exception.status_code, 400)

        with (
            patch.dict(server.MODEL_RUNTIME_CONFIG, {"target": {}}, clear=True),
            patch.object(server, "prune_controller_ocr_leases", return_value=False),
        ):
            with self.assertRaises(HTTPException) as acquire_cleanup:
                await server.acquire_controller_ocr_lease("target")
            with self.assertRaises(HTTPException) as release_cleanup:
                await server.release_controller_ocr_lease("expired")
        self.assertEqual(acquire_cleanup.exception.status_code, 503)
        self.assertEqual(release_cleanup.exception.status_code, 503)

        server.controller_ocr_leases.clear()
        self.assertFalse(await server.release_controller_ocr_lease("missing"))


if __name__ == "__main__":
    unittest.main()
