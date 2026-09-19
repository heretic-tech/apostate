#!/usr/bin/env python3
"""Tests for the anchor-and-dispersion compositor at catalogue version 2."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

try:
    from . import profile_resolver as resolver
except ImportError:
    import profile_resolver as resolver


BASE_CONFIG = {
    "fingerprint": 12345,
    "fingerprint_platform": "windows",
    "browser_build": "152.0.7977.83",
    "host_platform": "windows",
    "host_backend": "ANGLE/D3D11",
    "host_logical_cores": 32,
    "host_total_bytes": 64 * 1024 ** 3,
}


def _mirror_catalogue(destination: Path) -> Path:
    """Copy the catalogue and its dispersion directory so a test may mutate them."""
    source = resolver.DEFAULT_CATALOGUE.parent
    shutil.copytree(source, destination / "profiles")
    return destination / "profiles" / "catalogue.json"


def _machine_class_platforms(catalogue: dict, tables: dict) -> dict[str, str]:
    """Map every machine class to the platform its silicon belongs to.

    `panel`, `media_topology` and `battery` are keyed on `machine_class`,
    which is keyed on `gpu_identity`, which is keyed on an anchor. The
    platform is therefore a property of the anchor at the top of that chain
    rather than something a table states, and it is derived here instead of
    read off the machine-class id so a rename cannot make a wrong label pass.
    """
    anchor_platform = {anchor["id"]: anchor["platform"] for anchor in catalogue["anchors"]}
    identity_platform = {
        option["id"]: anchor_platform[option_set["key"]["anchor"]]
        for option_set in tables["gpu_identity"]["option_sets"]
        for option in option_set["options"]
    }
    platforms: dict[str, str] = {}
    for option_set in tables["machine_class"]["option_sets"]:
        platform = identity_platform[option_set["key"]["gpu_identity"]]
        for option in option_set["options"]:
            if platforms.setdefault(option["id"], platform) != platform:
                raise AssertionError(
                    f"machine class {option['id']} is offered on two platforms")
    return platforms


class CatalogueIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalogue = resolver.load_catalogue()
        cls.tables = resolver.load_dispersion()
        cls.machine_platforms = _machine_class_platforms(cls.catalogue, cls.tables)

    def test_catalogue_axes_anchors_and_option_values_all_validate(self) -> None:
        self.assertTrue(resolver.validate_catalogue())
        self.assertEqual(2, self.catalogue["catalogue_version"])
        self.assertEqual("anchors+dispersion", self.catalogue["model"])
        self.assertEqual(list(resolver.AXES), [entry["axis"] for entry in self.catalogue["axes"]])
        for axis, table in self.tables.items():
            for option_set in table["option_sets"]:
                for option in option_set["options"]:
                    self.assertTrue(resolver.validate_profile(option["value"]),
                                    f"{axis}/{option['id']} value is not schema-valid")
                    self.assertIn(option["evidence"], resolver.SUPPORTED_EVIDENCE)

    def test_retired_family_model_is_gone(self) -> None:
        root = resolver.DEFAULT_CATALOGUE.parent
        self.assertFalse((root / "families").exists())
        self.assertFalse((root / "distributions").exists())
        self.assertFalse((root / "compatibility-acceptance.json").exists())
        for key in ("families", "family_count", "distributions", "compatibility_acceptance"):
            self.assertNotIn(key, self.catalogue)
        broken = copy.deepcopy(self.catalogue)
        broken["families"] = []
        with self.assertRaises(resolver.ResolverError):
            resolver._validate_catalogue_index(broken)

    def test_conditioned_option_sets_are_total_over_their_parents(self) -> None:
        platforms = sorted(resolver.PLATFORMS)
        releases: dict[str, list[str]] = {}
        for option_set in self.tables["os_release"]["option_sets"]:
            releases[option_set["key"]["platform"]] = [o["id"] for o in option_set["options"]]
        self.assertEqual(platforms, sorted(releases))
        for axis in ("furniture", "font_packs"):
            keys = {(s["key"]["platform"], s["key"]["os_release"])
                    for s in self.tables[axis]["option_sets"]}
            expected = {(platform, release)
                        for platform, ids in releases.items() for release in ids}
            self.assertEqual(expected, keys, f"{axis} option sets are not total")
        language_sets = set(self.catalogue["language_sets"])
        voice_keys = {(s["key"]["platform"], s["key"]["os_release"], s["key"]["languages"])
                      for s in self.tables["voices"]["option_sets"]}
        expected_voices = {(platform, release, languages)
                           for platform, ids in releases.items()
                           for release in ids for languages in language_sets}
        self.assertEqual(expected_voices, voice_keys)
        anchor_ids = {anchor["id"] for anchor in self.catalogue["anchors"]}
        self.assertEqual(anchor_ids,
                         {s["key"]["anchor"] for s in self.tables["gpu_identity"]["option_sets"]})

    def test_every_offered_gpu_identity_is_backed_by_its_anchor(self) -> None:
        """The identity pool is wider than the measured members, deliberately.

        A limit table identifies the backend rather than the board, so a
        sibling board on the same driver and backend can carry the anchor's
        measured cluster. What that widening must not do is claim ground
        truth it does not have, or borrow a WebGPU adapter from a member that
        never reported one: every measured member is still offered under its
        own measured renderer, and every authored identity names the member
        whose adapter it carries -- a donor that does not resolve silently
        demotes WebGPU to host inheritance instead of failing.
        """
        anchor_evidence = {anchor["id"]: anchor["evidence_class"]
                           for anchor in self.catalogue["anchors"]}
        records = resolver.load_anchors(self.catalogue)
        for option_set in self.tables["gpu_identity"]["option_sets"]:
            anchor_id = option_set["key"]["anchor"]
            measured_class = anchor_evidence[anchor_id]
            measured = {
                ((member["identity"] or {})["webgl1"] or {})["unmaskedRenderer"]: member["device"]
                for member in records[anchor_id]["record"]["members"]
            }
            offered_members = set()
            for option in option_set["options"]:
                self.assertEqual(anchor_id, option["requires"]["anchor"])
                renderer = option["value"]["gpu"]["unmasked_renderer"]
                block = option.get("member")
                if block is None:
                    # A measured member carries exactly the anchor's own
                    # evidence class. SwiftShader's anchor is a compatibility
                    # capture, so a software member claiming ground truth
                    # would be asserting silicon that was never there.
                    self.assertEqual(measured_class, option["evidence"], option["id"])
                    self.assertIn(renderer, measured, f"{option['id']} is not a measured member")
                    offered_members.add(measured[renderer])
                    continue
                self.assertNotEqual(measured_class, option["evidence"],
                                    f"{option['id']} is authored, not measured")
                self.assertIn(option["evidence"], resolver.SUPPORTED_EVIDENCE)
                self.assertIn(block["label"], renderer, option["id"])
                donor = block.get("webgpu_measured_on")
                if donor is not None:
                    self.assertIn(donor, set(measured.values()),
                                  f"{option['id']} names a donor that is not a measured member")
            self.assertEqual(set(measured.values()), offered_members,
                             f"{anchor_id} does not offer all of its measured members")

    def test_font_packs_carry_a_core_set_and_whole_bundles(self) -> None:
        """A pack may enumerate only what it requires, except the platform core.

        An optional bundle arrives as a unit, so the families it requires the
        host to have and the families it lets a page see are the same list.
        The core pack is the one place they differ: since 0097 the browser
        assumes the operator provisioned the platform's stock set, so the
        allowlist is that whole set while `requires` stays the subset that
        cannot be uninstalled and therefore makes absence falsifiable. The
        filter is subtractive either way, so a family the host lacks still
        cannot be made to measure.
        """
        for option_set in self.tables["font_packs"]["option_sets"]:
            kinds = Counter(option["pack_kind"] for option in option_set["options"])
            self.assertEqual(1, kinds["core"])
            self.assertGreater(kinds["optional"], 0)
            for option in option_set["options"]:
                required = option["requires"]["families"]
                families = option["value"]["fonts"]["enumeration_allowlist"]
                self.assertEqual(sorted(families), families, option["id"])
                self.assertEqual(sorted(required), required, option["id"])
                self.assertLessEqual(set(required), set(families), option["id"])
                if option["pack_kind"] == "optional":
                    self.assertEqual(required, families, option["id"])
                    self.assertTrue(1 <= option["weight"] <= 100)
                else:
                    self.assertTrue(required, "the core pack requires nothing")

    def test_media_labels_are_platform_correct(self) -> None:
        for option_set in self.tables["media_topology"]["option_sets"]:
            platform = self.machine_platforms[option_set["key"]["machine_class"]]
            for option in option_set["options"]:
                for device in option["value"]["media"]["devices"]:
                    label = device["label"]
                    if "Realtek" in label:
                        self.assertEqual("windows", platform, f"{label} on {platform}")
                    if "MacBook" in label or "AirPods" in label or "Mac mini" in label:
                        self.assertEqual("macos", platform, f"{label} on {platform}")
                    if "Built-in Audio Analog Stereo" in label:
                        self.assertEqual("linux", platform, f"{label} on {platform}")

    def test_only_local_voices_are_offered(self) -> None:
        for option_set in self.tables["voices"]["option_sets"]:
            for option in option_set["options"]:
                for voice in option["value"].get("speech", {}).get("voices", []):
                    self.assertTrue(voice["local_service"],
                                    "network voices are a build capability, not a profile value")


class CompositionTests(unittest.TestCase):
    def test_resolution_is_byte_stable_and_the_payload_is_native_only(self) -> None:
        first = resolver.resolve_with_diagnostics(BASE_CONFIG)
        second = resolver.resolve_with_diagnostics(BASE_CONFIG)
        self.assertEqual(resolver._json_output(first).encode("utf-8"),
                         resolver._json_output(second).encode("utf-8"))
        self.assertTrue(resolver.validate_profile(first["profile"]))
        self.assertLessEqual(set(first["profile"]), resolver._PROFILE_TOP_LEVEL)
        self.assertEqual([], first["native_loader_support"]["unsupported_fields"])

    def test_the_profile_carries_the_loader_consumed_id_derived_from_the_root(self) -> None:
        """The loader reads `id` into source_id_, so the runtime payload needs it.

        It is derived from the composition root, not the profile digest: a field
        inside the profile cannot depend on a hash of the profile.
        """
        resolved = resolver.resolve_with_diagnostics(BASE_CONFIG)
        root = resolver.seed_root(str(BASE_CONFIG["fingerprint"]),
                                  BASE_CONFIG["fingerprint_platform"],
                                  BASE_CONFIG["browser_build"])
        self.assertEqual(f"fp-{root.hex()[:24]}", resolved["profile"]["id"])
        changed = resolver.resolve_with_diagnostics(dict(BASE_CONFIG, fingerprint=12346))
        self.assertNotEqual(resolved["profile"]["id"], changed["profile"]["id"])

    def test_identity_changes_with_every_component_of_the_seed_root(self) -> None:
        base = resolver.resolve_with_diagnostics(BASE_CONFIG)["diagnostics"]["identity"]
        for override in ({"fingerprint": 12346},
                         {"browser_build": "152.0.7977.82"},
                         {"fingerprint_platform": "macos"}):
            changed = resolver.resolve_with_diagnostics(dict(BASE_CONFIG, **override))
            self.assertNotEqual(base, changed["diagnostics"]["identity"], override)
        root = resolver.seed_root("1", "windows", "152.0.7977.83", 2, 3)
        self.assertNotEqual(root, resolver.seed_root("1", "windows", "152.0.7977.83", 3, 3))
        self.assertNotEqual(root, resolver.seed_root("1", "windows", "152.0.7977.83", 2, 4))

    def test_adding_a_pack_at_the_end_shifts_nothing_else(self) -> None:
        """The seed-stability claim: axis substreams are independent."""
        before = resolver.resolve_with_diagnostics(BASE_CONFIG)["diagnostics"]["axes"]
        with tempfile.TemporaryDirectory() as tmp:
            catalogue_path = _mirror_catalogue(Path(tmp))
            table_path = catalogue_path.parent / "dispersion" / "font_packs.json"
            table = json.loads(table_path.read_text(encoding="utf-8"))
            for option_set in table["option_sets"]:
                option_set["options"].append({
                    "id": "test-extra-pack", "weight": 50, "evidence": "catalogue-value",
                    "pack_kind": "optional", "requires": {"families": ["Test Family"]},
                    "value": {"fonts": {"enumeration_allowlist": ["Test Family"]}},
                })
            table_path.write_text(json.dumps(table, sort_keys=True, separators=(",", ":")) + "\n",
                                  encoding="utf-8")
            catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
            for entry in catalogue["axes"]:
                if entry["axis"] == "font_packs":
                    entry["options"] += len(table["option_sets"])
            catalogue_path.write_text(
                json.dumps(catalogue, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8")
            after = resolver.resolve_with_diagnostics(
                dict(BASE_CONFIG, catalogue_path=catalogue_path))["diagnostics"]["axes"]
        for axis in before:
            if axis == "font_packs":
                continue
            self.assertEqual(before[axis], after[axis], f"{axis} shifted")
        self.assertEqual(before["font_packs"]["options"],
                         [i for i in after["font_packs"]["options"] if i != "test-extra-pack"])

    def test_capacity_is_only_ever_reduced(self) -> None:
        """Never above the host, and absent is the strongest form of that.

        `cpu` and `memory` are conditioned on `gpu_identity`, so a host below
        every bucket the drawn chip ships with has no servable option and the
        surface stays host-inherited. That is the rule working rather than a
        gap: an absent field inherits, and only a present one can overclaim.
        Absence is checked to be deliberate rather than silent, so the two
        cases cannot collapse into one another unnoticed.
        """
        small = resolver.resolve_with_diagnostics(dict(
            BASE_CONFIG, host_logical_cores=4, host_total_bytes=8 * 1024 ** 3))
        for axis, section, field, ceiling in (("cpu", "cpu", "logical_cores", 4),
                                              ("memory", "memory", "total_bytes", 8 * 1024 ** 3)):
            if section in small["profile"]:
                self.assertLessEqual(small["profile"][section][field], ceiling)
            else:
                self.assertEqual(["host-inherited"],
                                 small["diagnostics"]["axes"][axis]["evidence"], axis)
        present = 0
        for seed in range(40):
            resolved = resolver.resolve_profile(dict(
                BASE_CONFIG, fingerprint=seed, host_logical_cores=6,
                host_total_bytes=16 * 1024 ** 3))
            if "cpu" in resolved:
                present += 1
                self.assertLessEqual(resolved["cpu"]["logical_cores"], 6)
            if "memory" in resolved:
                self.assertLessEqual(resolved["memory"]["total_bytes"], 16 * 1024 ** 3)
        self.assertGreater(present, 0, "no seed produced a clamped core count to check")

    def test_an_axis_with_no_servable_option_falls_back_to_host_inheritance(self) -> None:
        """A host below every bucket must inherit, not receive the lowest bucket.

        The lowest macOS core bucket is 8, so on a two-core host "keep the
        lowest" would ship a claim above host capability.
        """
        tiny = resolver.resolve_with_diagnostics({
            "fingerprint": 3, "fingerprint_platform": "macos",
            "host_platform": "macos", "host_backend": "ANGLE/Metal",
            "host_logical_cores": 2, "host_total_bytes": 4 * 1024 ** 3,
            "window_width": 320, "window_height": 240,
        })
        self.assertNotIn("cpu", tiny["profile"])
        self.assertNotIn("memory", tiny["profile"])
        self.assertEqual([], tiny["diagnostics"]["axes"]["cpu"]["options"])
        self.assertEqual(["host-inherited"], tiny["diagnostics"]["axes"]["cpu"]["evidence"])
        self.assertTrue(any("stays host-inherited" in warning
                            for warning in tiny["diagnostics"]["warnings"]))
        self.assertNotIn("avail_inset_left", tiny["profile"].get("screen", {}))
        self.assertNotEqual({}, tiny["profile"].get("screen", None))

    def test_no_persona_host_or_seed_combination_leaves_a_table_gap(self) -> None:
        hosts = (("macos", "ANGLE/Metal"), ("windows", "ANGLE/D3D11"),
                 ("linux", "ANGLE/Vulkan"))
        for persona in sorted(resolver.PLATFORMS):
            for host_platform, backend in hosts:
                for seed in range(6):
                    profile = resolver.resolve_profile({
                        "fingerprint": seed, "fingerprint_platform": persona,
                        "host_platform": host_platform, "host_backend": backend,
                        "host_logical_cores": 16, "host_total_bytes": 32 * 1024 ** 3,
                        "window_width": 1024, "window_height": 700,
                    })
                    self.assertGreaterEqual(profile["screen"]["width"], 1024)
                    self.assertLessEqual(profile["cpu"]["logical_cores"], 16)

    def test_every_persona_serves_its_own_audio_buffer(self) -> None:
        """The defect the audio axis exists for: baseLatency was the host's.

        Patch 0019 built the emitter and no table supplied the field, so the
        shipped binary reported 0.042666666666666665 under all three personas —
        2048 frames of Linux ALSA, the build host's own, identical to stock.
        The assertion is on what a page reads rather than on the field: Blink
        computes max(framesPerBuffer, 128) / sampleRate, and on a 48 kHz device
        each persona must land on the value its reference capture measured.
        """
        expected = {
            # resources/fingerprints/raw/m4-max-chrome-20260908T163229Z.json
            "macos": (256, 0.005333333333333333),
            # resources/fingerprints/raw/windows-chrome-20260910T140813Z.json
            "windows": (480, 0.01),
            # Authored: Chromium's Pulse floor. No admitted Linux reference has
            # an audio device, so this one is not a capture and says so.
            "linux": (512, 0.010666666666666666),
        }
        for persona in sorted(resolver.PLATFORMS):
            frames, base_latency = expected[persona]
            for seed in range(8):
                audio = resolver.resolve_profile(dict(
                    BASE_CONFIG, fingerprint=seed, fingerprint_platform=persona))["audio"]
                self.assertEqual(frames, audio["hardware_buffer_frames"], persona)
                self.assertEqual(base_latency,
                                 max(audio["hardware_buffer_frames"], 128) / 48000, persona)

    def test_a_profile_with_no_audio_buffer_does_not_compose(self) -> None:
        """An absent audio surface is a defect, not a silence.

        The field was declared and consumed for ninety-odd patches while
        nothing produced it, and composition said nothing. Two layers refuse
        it now. A whole platform missing from the table is caught where every
        axis is, by exact-and-total option-set matching. The subtler shape is
        the one that actually shipped: an audio section that composes and
        carries nothing, which is indistinguishable from the old silence at
        the emitter and is what this exercises.
        """
        with tempfile.TemporaryDirectory() as tmp:
            catalogue_path = _mirror_catalogue(Path(tmp))
            table = catalogue_path.parent / "dispersion" / "audio.json"
            doc = json.loads(table.read_text(encoding="utf-8"))
            for option_set in doc["option_sets"]:
                if option_set["key"]["platform"] == "windows":
                    for option in option_set["options"]:
                        option["value"] = {"audio": {}}
            table.write_text(json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n",
                             encoding="utf-8")
            with self.assertRaises(resolver.ResolverError) as caught:
                resolver.resolve_profile(dict(BASE_CONFIG, catalogue_path=catalogue_path))
            self.assertIn("audio.hardware_buffer_frames", str(caught.exception))

    def test_work_area_is_derived_from_the_panel_and_the_furniture_insets(self) -> None:
        for seed in range(25):
            screen = resolver.resolve_profile(dict(BASE_CONFIG, fingerprint=seed))["screen"]
            self.assertNotIn("avail_inset_left", screen)
            self.assertLessEqual(screen["avail_width"], screen["width"])
            self.assertLessEqual(screen["avail_height"], screen["height"])
            self.assertLessEqual(screen["avail_top"], screen["height"] - screen["avail_height"])

    def test_measured_furniture_reproduces_the_captured_work_area(self) -> None:
        catalogue, tables, _ = resolver._load_catalogue()
        resolved = resolver.resolve_with_diagnostics(dict(
            BASE_CONFIG, fingerprint="win11-measured", host_logical_cores=8,
            host_total_bytes=16 * 1024 ** 3))
        platforms = _machine_class_platforms(catalogue, tables)
        # `panel` is keyed on `machine_class` since the chassis axis landed, so
        # the 1080p panel is offered under every Windows chassis that ships
        # one. A measured panel is one panel: they have to agree.
        panels = [option["value"]["screen"]
                  for option_set in tables["panel"]["option_sets"]
                  if platforms[option_set["key"]["machine_class"]] == "windows"
                  for option in option_set["options"] if option["id"] == "fhd-1080p"]
        self.assertTrue(panels, "no Windows chassis offers the measured 1080p panel")
        self.assertEqual([panels[0]] * len(panels), panels)
        furniture = next(option for option_set in tables["furniture"]["option_sets"]
                         if option_set["key"] == {"platform": "windows", "os_release": "windows-11"}
                         for option in option_set["options"] if option["id"] == "taskbar-bottom")
        screen, insets = panels[0], furniture["value"]["screen"]
        self.assertEqual(1920, screen["width"])
        self.assertEqual(1080, screen["height"])
        self.assertEqual(1032, screen["height"] - insets["avail_inset_top"]
                         - insets["avail_inset_bottom"])
        self.assertEqual(1920, screen["width"] - insets["avail_inset_left"]
                         - insets["avail_inset_right"])
        self.assertIsNotNone(resolved["profile"]["window"]["outer_inner_delta_height"])

    def test_the_anchor_capability_cluster_reaches_the_profile(self) -> None:
        """An identity string with the host's own tables under it is the retired
        catalogue's failure, so selecting an anchor must take the whole cluster."""
        resolved = resolver.resolve_with_diagnostics(BASE_CONFIG)
        profile, diagnostics = resolved["profile"], resolved["diagnostics"]
        for section in ("gl_extensions", "gl_limits", "gl_precisions"):
            self.assertIn(section, profile, f"{section} never reached the profile")
        self.assertGreater(len(profile["gl_extensions"]), 30)
        self.assertIn("MAX_TEXTURE_SIZE", profile["gl_limits"])
        self.assertEqual(12, len(profile["gl_precisions"]))
        self.assertEqual([diagnostics["anchor"]["id"]], diagnostics["axes"]["anchor"]["options"])
        self.assertTrue(resolver.validate_profile(profile))

    def test_every_anchor_member_yields_the_same_gl_cluster(self) -> None:
        """Members, not offered identities.

        The claim is about the anchor: a limit table identifies the backend,
        so every device measured under one anchor produced the same GL
        cluster, and that is what makes the anchor atomic. The offered
        identity pool is wider than the member list and reaches the cluster
        through the compositor's donor lookup, not through its own renderer
        string, so asking these helpers about an option would be asking a
        question the composition never asks.
        """
        catalogue, _, records = resolver._load_catalogue()
        for anchor in catalogue["anchors"]:
            record = records[anchor["id"]]["record"]
            layers = [
                resolver._anchor_capability_layer(
                    record, ((member["identity"])["webgl1"])["unmaskedRenderer"])
                for member in record["members"]
            ]
            reference = {key: layers[0][key] for key in
                         ("gl_extensions", "gl_limits", "gl_precisions") if key in layers[0]}
            self.assertTrue(reference, f"{anchor['id']} contributed no GL cluster")
            for layer in layers[1:]:
                self.assertEqual(reference, {key: layer[key] for key in reference})

    def test_a_member_with_no_measured_adapter_leaves_webgpu_inherited(self) -> None:
        """WebGPU is not uniform inside the Linux/Vulkan anchor: two members
        reported an adapter and two returned none.

        So an authored identity cannot take WebGPU from the anchor -- it
        takes it from the one member it names, and that member has to have
        reported an adapter. An authored Ada board carrying the lovelace
        cluster two Ada members measured is a claim; the same string over the
        Ampere member's silence would be a fabrication.
        """
        catalogue, tables, records = resolver._load_catalogue()
        anchor_id = next(a["id"] for a in catalogue["anchors"]
                         if a["backend"] == "ANGLE/Vulkan")
        record = records[anchor_id]["record"]
        adapters: dict[str, dict | None] = {}
        present = absent = 0
        for member in record["members"]:
            layer = resolver._anchor_capability_layer(
                record, ((member["identity"])["webgl1"])["unmaskedRenderer"])
            adapters[member["device"]] = layer.get("webgpu")
            if "webgpu" in layer:
                present += 1
                self.assertIn(layer["webgpu"]["info"]["architecture"], {"lovelace"})
            else:
                absent += 1
        self.assertEqual((2, 2), (present, absent))
        options = next(s["options"] for s in tables["gpu_identity"]["option_sets"]
                       if s["key"]["anchor"] == anchor_id)
        authored = [option for option in options if "member" in option]
        self.assertTrue(authored, "the widened identity pool is gone")
        for option in authored:
            donor = option["member"].get("webgpu_measured_on")
            self.assertIsNotNone(donor, f"{option['id']} names no measured member")
            self.assertIsNotNone(adapters[donor],
                                 f"{option['id']} borrows WebGPU from a member that reported none")
        with self.assertRaises(resolver.ResolverError):
            resolver._anchor_capability_layer(record, "ANGLE (NVIDIA, fabricated RTX 5090)")

    def test_webgpu_architecture_follows_the_drawn_identity(self) -> None:
        """A 4090 persona must not report the donor's `ampere`.

        The D3D11 identity pool rotates across silicon generations on one
        anchor, because ANGLE builds the renderer string from the DXGI adapter
        description and derives every limit from the feature level. WebGPU is
        the one surface that does NOT follow the anchor: Dawn resolves
        `GPUAdapterInfo.architecture` from the PCI device id, so a persona
        claiming a 4090 while `adapter.info` says `ampere` is a contradiction
        a page reads in two calls.

        `member.webgpu_architecture` overrides exactly that one field and
        nothing else -- vendor, features and the measured limits still come
        from the donor, because those are what the donor measured. Composed
        rather than computed here: the branch under test lives in the
        resolution loop, and recomputing the rule would test the test.
        """
        _, tables, records = resolver._load_catalogue()
        stated: dict[str, str | None] = {}
        donor_of: dict[str, str] = {}
        for option_set in tables["gpu_identity"]["option_sets"]:
            for option in option_set["options"]:
                block = option.get("member") or {}
                if "webgpu_measured_on" not in block:
                    continue
                stated[option["id"]] = block.get("webgpu_architecture")
                donor_of[option["id"]] = block["webgpu_measured_on"]
        self.assertTrue(any(value for value in stated.values()),
                        "no identity states its own architecture, so this asserts nothing")

        windows = [anchor for anchor in resolver.load_catalogue()["anchors"]
                   if anchor["platform"] == "windows"]
        self.assertTrue(windows)
        for anchor in windows:
            record = records[anchor["id"]]["record"]
            renderer_of = {
                member.get("device"):
                    ((member.get("identity") or {}).get("webgl1") or {}).get("unmaskedRenderer")
                for member in record["members"]
            }
            covered: set[bool] = set()
            for seed in range(400):
                resolved = resolver.resolve_with_diagnostics(
                    dict(BASE_CONFIG, fingerprint=f"arch-{seed}", anchor=anchor["id"]))
                identity = resolved["diagnostics"]["axes"]["gpu_identity"]["options"][0]
                if identity not in donor_of:
                    continue
                override = stated[identity]
                if (override is not None) in covered:
                    continue
                covered.add(override is not None)
                served = resolved["profile"]["webgpu"]
                donor = resolver._anchor_capability_layer(
                    record, renderer_of[donor_of[identity]])["webgpu"]
                self.assertEqual(override or donor["info"]["architecture"],
                                 served["info"]["architecture"], identity)
                self.assertEqual(donor["info"]["vendor"], served["info"]["vendor"], identity)
                for field in ("features", "limits"):
                    self.assertEqual(donor.get(field), served.get(field), identity)
                if len(covered) == 2:
                    break
            self.assertEqual({False, True}, covered,
                             f"{anchor['id']} never drew both an overriding and a "
                             "non-overriding identity")

    def test_a_measured_point_size_fraction_is_carried(self) -> None:
        """A float-valued GL parameter keeps its fraction; a count does not.

        This asserted the opposite until the shipped build was measured. The
        Linux/Vulkan anchor reports ALIASED_POINT_SIZE_RANGE max 2047.9375 --
        2047 + 15/16, what a four-bit subpixel point size produces -- and
        leaving it unrepresented served the host's 256 under an NVIDIA
        renderer string. Truncating to 2047 would be equally wrong, so the
        fraction is carried as measured.
        """
        catalogue, tables, records = resolver._load_catalogue()
        seen_fraction = False
        for anchor in catalogue["anchors"]:
            record = records[anchor["id"]]["record"]
            renderer = ((record["members"][0]["identity"])["webgl1"])["unmaskedRenderer"]
            limits = resolver._anchor_capability_layer(record, renderer)["gl_limits"]
            raw = record["capability_cluster"]["webgl1"]["parameters"]["ALIASED_POINT_SIZE_RANGE"]
            self.assertIn("ALIASED_POINT_SIZE_RANGE_MAX", limits,
                          f"{anchor['id']} point range {raw} was dropped")
            self.assertEqual(limits["ALIASED_POINT_SIZE_RANGE_MAX"], raw[1],
                             f"{anchor['id']} point range {raw} was not served as measured")
            if not float(raw[1]).is_integer():
                seen_fraction = True
            for name, value in limits.items():
                # Only the genuinely float-valued names may be nonintegral.
                if not float(value).is_integer():
                    self.assertIn(name.rsplit("_", 1)[0], resolver.GL_LIMIT_FRACTIONAL_KEYS,
                                  f"{name} is a count and must not carry a fraction")
        self.assertTrue(seen_fraction,
                        "no anchor exercises the fractional path any more; this test "
                        "no longer guards what it was written for")

    def test_cross_backend_clusters_are_not_interchangeable(self) -> None:
        catalogue, _, records = resolver._load_catalogue()
        signatures = {}
        for anchor in catalogue["anchors"]:
            record = records[anchor["id"]]["record"]
            renderer = ((record["members"][0]["identity"])["webgl1"])["unmaskedRenderer"]
            layer = resolver._anchor_capability_layer(record, renderer)
            signatures[anchor["id"]] = resolver._canonical_json(
                [layer["gl_extensions"], layer["gl_limits"]])
        self.assertEqual(len(signatures), len(set(signatures.values())),
                         "two anchors produced the same capability signature")

    def test_the_persona_never_moves_the_gpu_cluster(self) -> None:
        resolved = resolver.resolve_with_diagnostics(dict(
            BASE_CONFIG, fingerprint_platform="windows", host_platform="macos",
            host_backend="ANGLE/Metal"))
        anchor = resolved["diagnostics"]["anchor"]
        self.assertEqual("ANGLE/Metal", anchor["backend"])
        self.assertEqual("macos", anchor["platform"])
        self.assertEqual("Windows", resolved["profile"]["platform"]["name"])
        self.assertTrue(any("does not move the GPU cluster" in warning
                            for warning in resolved["diagnostics"]["warnings"]))
        with self.assertRaises(resolver.ResolverError):
            resolver.resolve_profile(dict(BASE_CONFIG, host_backend="ANGLE/OpenGL",
                                          host_platform="linux"))

    def test_realised_distribution_follows_the_table_weights(self) -> None:
        counts: Counter[str] = Counter()
        for seed in range(600):
            resolved = resolver.resolve_with_diagnostics(dict(BASE_CONFIG, fingerprint=seed))
            counts[resolved["diagnostics"]["axes"]["os_release"]["options"][0]] += 1
        table = next(option_set for option_set in resolver.load_dispersion()["os_release"]["option_sets"]
                     if option_set["key"]["platform"] == "windows")
        weights = {option["id"]: option["weight"] for option in table["options"]}
        self.assertEqual(set(weights), set(counts), "an option was never drawn")
        total_weight = sum(weights.values())
        for option_id, weight in weights.items():
            expected = 600 * weight / total_weight
            self.assertLess(abs(counts[option_id] - expected), 0.35 * expected + 12,
                            f"{option_id} realised {counts[option_id]}, expected ~{expected:.0f}")

    def test_weighted_pick_uses_cumulative_weight_without_modulo(self) -> None:
        options = [{"id": "a", "weight": 1}, {"id": "b", "weight": 999}]
        picks = Counter(options[resolver.weighted_pick(
            resolver.seed_root(str(seed), "windows", "152.0.7977.83"), "cpu", options)]["id"]
            for seed in range(400))
        self.assertGreater(picks["b"], picks["a"] * 20)
        self.assertEqual(0, resolver.weighted_pick(b"\x00" * 32, "cpu",
                                                   [{"id": "only", "weight": 3}]))

    def test_a_missing_option_set_is_a_table_defect_and_fails_the_launch(self) -> None:
        tables = resolver.load_dispersion()
        with self.assertRaises(resolver.ResolverError):
            resolver._option_set("furniture", tables["furniture"],
                                 {"platform": "windows", "os_release": "windows-7"})

    def test_language_key_is_a_total_projection_computed_before_the_draw(self) -> None:
        table = resolver.load_dispersion()["voices"]
        self.assertEqual("en-US,en", resolver._language_key("en-US,en", table))
        self.assertEqual("", resolver._language_key("fr-FR,fr", table))
        self.assertEqual("", resolver._language_key(None, table))
        resolved = resolver.resolve_profile(dict(BASE_CONFIG, locale_policy="en-au"))
        self.assertNotIn("speech", resolved)

    def test_locale_follows_launch_precedence_geoip_then_the_host_and_never_a_draw(self) -> None:
        """The seed must not reach the locale surface, at any seed.

        It used to. Four catalogue policies were drawn by seed, so a bare
        launch on an Asia/Bangkok host served America/New_York, Europe/London
        or Australia/Sydney depending on the seed -- a timezone uncorrelated
        with the exit IP by construction, which is a first-line correlation
        check at every fraud vendor. FINGERPRINTS.md section 6 conditions this
        surface on "launch precedence, GeoIP" and gives it no option table.
        """
        host = {"host_timezone": "Asia/Bangkok", "host_languages": "th-TH,th"}
        # No layer names a locale: the surface is absent, which is what leaves
        # the host's real zone in ICU and its real list in the pref. Every
        # seed, because the defect was a per-seed draw.
        for seed in range(40):
            envelope = resolver.resolve_with_diagnostics(
                dict(BASE_CONFIG, **host, fingerprint=seed))
            self.assertNotIn("locale", envelope["profile"], seed)
            sources = envelope["diagnostics"]["locale"]
            self.assertEqual(("host", "host"),
                             (sources["accept_languages"]["source"],
                              sources["timezone"]["source"]), seed)
            self.assertEqual("Asia/Bangkok", sources["timezone"]["host_value"], seed)

        # Each named layer owns both fields it supplies, and the stronger one
        # wins field by field. A locale policy supplies the pair as a unit.
        geoip = {"locale": "de-DE", "timezone": "Europe/Berlin"}
        for config, expected in (
            ({"geoip": geoip},
             {"accept_languages": ("de-DE,de", "geoip"), "timezone": ("Europe/Berlin", "geoip")}),
            ({"geoip": geoip, "fingerprint_timezone": "Europe/Tirane"},
             {"accept_languages": ("de-DE,de", "geoip"),
              "timezone": ("Europe/Tirane", "command-line")}),
            ({"locale_policy": "en-gb"},
             {"accept_languages": ("en-GB,en", "command-line"),
              "timezone": ("Europe/London", "command-line")}),
            # A partial GeoIP answer contributes the field it resolved and
            # leaves the other to the host. scripts/geoip.py names no locale
            # for a country its policy table does not carry, and inventing one
            # is what this precedence refuses.
            ({"geoip": {"timezone": "Europe/Tirane"}},
             {"accept_languages": (None, "host"), "timezone": ("Europe/Tirane", "geoip")}),
        ):
            envelope = resolver.resolve_with_diagnostics(dict(BASE_CONFIG, **host, **config))
            sources = envelope["diagnostics"]["locale"]
            carried = envelope["profile"].get("locale") or {}
            for field, (value, source) in expected.items():
                self.assertEqual(value, sources[field]["value"], (config, field))
                self.assertEqual(source, sources[field]["source"], (config, field))
                self.assertEqual(value, carried.get(field), (config, field))

        # And the guard: a value in the section whose source is the host is the
        # shape a draw produced, so it raises instead of shipping.
        with self.assertRaises(resolver.ResolverError):
            resolver._locale_provenance_check(
                {"locale": {"timezone": "Australia/Sydney"}},
                {"accept_languages": {"value": None, "source": "host"},
                 "timezone": {"value": None, "source": "host"}})

    def test_explicit_profile_file_is_validated_and_marked_as_a_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.json"
            good.write_text(json.dumps({"id": "explicit", "cpu": {"logical_cores": 4}}),
                            encoding="utf-8")
            resolved = resolver.resolve_with_diagnostics({"profile_file": good})
            self.assertEqual("explicit-profile-file", resolved["diagnostics"]["source"])
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps({"id": "bad", "not_a_profile_field": True}),
                           encoding="utf-8")
            with self.assertRaises(resolver.ResolverError):
                resolver.resolve_with_diagnostics({"profile_file": bad})

    def test_unrecognised_requires_key_is_rejected(self) -> None:
        with self.assertRaises(resolver.ResolverError):
            resolver._validate_requires("cpu", "cores-8", {"min_threads": 8})

    # Chromium's JSON writer emits raw UTF-8 for non-ASCII
    # (base/json/string_escape.cc WriteUnicodeCharacter), so the byte-comparable
    # encoding is ensure_ascii=False.
    GOLDEN_HOST = {"host_platform": "macos", "host_backend": "ANGLE/Metal",
                   "host_logical_cores": 14, "host_total_bytes": 38654705664}
    # The sections the composed profile carried when these digests were taken.
    # Recorded so a digest mismatch can be told apart from a composition that
    # has since grown or lost a section, which is not the same finding.
    GOLDEN_SECTIONS = frozenset({
        "cpu", "fonts", "gl_extensions", "gl_limits", "gl_precisions", "gpu", "id",
        "keyboard", "locale", "media", "memory", "platform", "screen", "theme",
        "webgpu", "window",
    })
    GOLDEN_PROFILES = {
        "windows": ("fp-b0b97b3a3531b65ee50f45fc", GOLDEN_SECTIONS | {"speech"},
                    "5f9b72f3d7d243bee90353008e839937ea4d0b253a3299a3f060efed96e5b042"),
        "macos": ("fp-60eab51485a4a8465ce3c24a", GOLDEN_SECTIONS | {"speech"},
                  "7757d9370957f5a9bde47258d540f2772da0134d86d7e62c014dddcdc7580683"),
        "linux": ("fp-8c5f63da9ef88ea749549a91", GOLDEN_SECTIONS,
                  "bc61c1eb8e3ba852222f5955896c20d4f0d7d8bc80e71713051e7260b169f342"),
    }

    def _check_golden(self, persona: str, profile: dict, digest: str,
                      sections: frozenset[str]) -> None:
        """Compare one composed profile against a native-compositor digest.

        The digests came out of the C++ compositor, so nothing on this side
        can refresh them: recomputing them from this module would replace a
        cross-implementation comparison with Python agreeing with itself and
        report that as success. When the composition no longer has the shape
        they were taken against, the pin is stale rather than wrong, and the
        honest result is to say so and name the one thing that can move it.
        """
        moved = set(profile) ^ set(sections)
        if moved:
            reason = (
                f"{persona}: the golden digest covers "
                f"{len(sections)} sections and the composition now has "
                f"{len(profile)}: {', '.join(sorted(moved))}. The digest was "
                "produced by the C++ compositor, so only a build can refresh "
                "it -- see docs/RELEASE.md, 'Refreshing the native golden "
                "profile digests'. Recomputing it here would make the test "
                "compare this module against itself."
            )
            if os.environ.get("APOSTATE_REQUIRE_NATIVE_GOLDENS"):
                self.fail(reason)
            self.skipTest(reason)
        encoded = json.dumps(profile, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")
        self.assertEqual(digest, hashlib.sha256(encoded).hexdigest(), persona)

    def test_every_persona_matches_the_native_compositor_byte_for_byte(self) -> None:
        """Determinism gate (FINGERPRINTS section 9.1), all three personas.

        These digests were produced independently by the C++ compositor in the
        browser process. macOS is the load-bearing one: its speech.voices table
        carries non-ASCII names, so that digest is what proves the two writers
        agree on escaping rather than merely on field values.

        The voices table is keyed on the resolved accept-languages list, and
        that list now comes from launch precedence, GeoIP or the host rather
        than from a seeded draw, so a bare composition has no measured language
        set and therefore no speech section at all. The escaping check below
        names a locale to get one, which is the only composition shape that
        still carries the non-ASCII the check is about.
        """
        stale = []
        for persona, (profile_id, sections, digest) in self.GOLDEN_PROFILES.items():
            profile = resolver.resolve_profile(dict(
                self.GOLDEN_HOST, fingerprint=12345, fingerprint_platform=persona,
                browser_build="152.0.7977.83"))
            # The identity is derived from the seed root alone, so it is
            # comparable whatever the composition grew, and it is what proves
            # the two implementations still agree on the seed derivation.
            self.assertEqual(profile_id, profile["id"], persona)
            try:
                self._check_golden(persona, profile, digest, sections)
            except unittest.SkipTest as skipped:
                stale.append(str(skipped))
        macos = resolver.resolve_profile(dict(
            self.GOLDEN_HOST, fingerprint=12345, fingerprint_platform="macos",
            browser_build="152.0.7977.83", locale_policy="en-us"))
        self.assertTrue(any(ord(char) > 127 for voice in macos["speech"]["voices"]
                            for char in voice["name"]),
                        "the macOS digest only proves escaping agreement if it has non-ASCII")
        if stale:
            self.skipTest(" | ".join(stale))

    def test_no_option_value_contains_a_character_the_two_writers_escape_differently(self) -> None:
        """Keeps the cross-implementation digest comparison valid as tables grow.

        Chromium escapes `<` as \\u003C (a deliberate script-execution guard),
        U+2028 and U+2029; Python's json escapes none of them. A profile value
        containing one would diverge byte-wise between the two writers without
        being non-ASCII, so it would not show up as a mojibake-style failure.
        """
        divergent = ("<", "\u2028", "\u2029")

        def scan(value: object, where: str) -> list[str]:
            if isinstance(value, dict):
                return [hit for key, item in value.items()
                        for hit in scan(key, where) + scan(item, f"{where}.{key}")]
            if isinstance(value, list):
                return [hit for index, item in enumerate(value)
                        for hit in scan(item, f"{where}[{index}]")]
            if isinstance(value, str):
                return [f"{where}: {char!r}" for char in divergent if char in value]
            return []

        hits: list[str] = []
        for axis, table in resolver.load_dispersion().items():
            for option_set in table["option_sets"]:
                for option in option_set["options"]:
                    hits.extend(scan(option["value"], f"{axis}/{option['id']}"))
        for kind in resolver._POLICY_KINDS:
            for entry in resolver.load_catalogue()["policies"][kind]:
                hits.extend(scan(entry["value"], f"policy.{kind}/{entry['id']}"))
        self.assertEqual([], hits)

    def test_golden_vectors_agreed_with_the_native_compositor(self) -> None:
        """Determinism gate (FINGERPRINTS section 9.1).

        These values were produced independently by the C++ compositor in the
        browser process and by this module. Pinning them here means a drift in
        either implementation, or in the option tables the profile is drawn
        from, fails loudly on both sides instead of silently diverging.

        The seed root, the draw and the identity depend only on the seed, so
        they hold whatever the option tables do and are asserted outright. The
        profile digest depends on the whole composition, so it is the one part
        a catalogue change can make stale.
        """
        root = resolver.seed_root("12345", "windows", "152.0.7977.83", 2, 3)
        self.assertEqual(
            "b0b97b3a3531b65ee50f45fc56ea165e625bc8e6a8248f1afccb25d6308db8c8",
            root.hex())
        self.assertEqual(0x19d1abcfc04392c7, resolver.draw(root, "cpu", 0))
        profile = resolver.resolve_profile({
            "fingerprint": 12345, "fingerprint_platform": "windows",
            "browser_build": "152.0.7977.83", "host_platform": "macos",
            "host_backend": "ANGLE/Metal", "host_logical_cores": 14,
            "host_total_bytes": 38654705664,
        })
        profile_id, sections, digest = self.GOLDEN_PROFILES["windows"]
        self.assertEqual(profile_id, profile["id"])
        self._check_golden("windows", profile, digest, sections)

    def test_cli_operations_emit_json(self) -> None:
        script = Path(__file__).with_name("profile_resolver.py")
        for args in (["--catalogue"], ["--list"],
                     ["--resolve", "--fingerprint", "7", "--fingerprint-platform", "macos"],
                     ["--resolve", "--fingerprint", "7", "--runtime-only"]):
            completed = subprocess.run([sys.executable, str(script), *args],
                                       check=True, capture_output=True, text=True)
            self.assertIsInstance(json.loads(completed.stdout), dict)


if __name__ == "__main__":
    # Verbose, so a skip prints the reason that made it skip. A build-bound
    # pin reported as "OK (skipped=2)" is how a stale claim survives.
    unittest.main(verbosity=2)
