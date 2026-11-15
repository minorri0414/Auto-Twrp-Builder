from twrpdtgen.device_tree import DeviceTree
import argparse
import os
import shutil
from pathlib import Path

from sebaubuntu_libs.libaik import AIKManager

# The stock boot ramdisk of many OPPO/Qualcomm devices does not carry
# default.prop/prop.default (the props live in /system), and it names its
# fstab fstab.qcom instead of recovery.fstab.  twrpdtgen needs both, so inject
# them into the unpacked ramdisk before the device tree is generated.
DEFAULT_PROPS = """\
ro.product.manufacturer=OPPO
ro.product.brand=OPPO
ro.product.model=PDPM00
ro.product.device=pdpm00
ro.product.name=PDPM00
ro.product.board=sm7250
ro.board.platform=sm7250
ro.product.cpu.abilist=arm64-v8a,armeabi-v7a,armeabi
ro.product.cpu.abilist64=arm64-v8a
ro.product.cpu.abilist32=armeabi-v7a,armeabi
ro.product.first_api_level=29
ro.build.ab_update=false
ro.boot.dynamic_partitions=true
ro.build.version.security_patch=2021-11-05
ro.vendor.build.security_patch=2021-11-05
ro.build.fingerprint=OPPO/PDPM00/PDPM00:11/RKQ1.200903.002/2107010000:user/release-keys
ro.build.description=PDPM00-user 11 RKQ1.200903.002 2107010000 release-keys
ro.minui.pixel_format=RGBX_8888
"""

FSTAB_CANDIDATES = (
    "fstab.qcom",
    "fstab.default",
    "fstab.emmc",
    "fstab.hardware",
    "oplus.fstab",
)

_original_unpackimg = AIKManager.unpackimg


def _inject_ramdisk_files(self, image, ignore_ramdisk_errors=False):
    image_info = _original_unpackimg(
        self, image, ignore_ramdisk_errors=ignore_ramdisk_errors
    )
    ramdisk = image_info.ramdisk
    if ramdisk is None:
        return image_info

    if not (ramdisk / "default.prop").is_file() and not (ramdisk / "prop.default").is_file():
        (ramdisk / "prop.default").write_text(DEFAULT_PROPS)

    target_fstab = ramdisk / "etc" / "recovery.fstab"
    if not target_fstab.is_file():
        for name in FSTAB_CANDIDATES:
            candidate = ramdisk / name
            if candidate.is_file():
                target_fstab.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(candidate, target_fstab)
                break

    return image_info


AIKManager.unpackimg = _inject_ramdisk_files


def _patch_device_tree(device_tree_path: Path):
    """Add the bits a generated tree needs for this dynamic-partition device."""
    device_mk = device_tree_path / "device.mk"
    device_mk.write_text(
        device_mk.read_text()
        + "\n# OPPO PDPM00 (sm7250, dynamic partitions)\n"
        + "PRODUCT_USE_DYNAMIC_PARTITIONS := true\n"
        + "PRODUCT_SHIPPING_API_LEVEL := 30\n"
    )

    board_config = device_tree_path / "BoardConfig.mk"
    board_config.write_text(
        board_config.read_text()
        + "\n# Generated for OPPO PDPM00\n"
        + "TARGET_RECOVERY_FSTAB := $(DEVICE_PATH)/recovery.fstab\n"
        + "TW_INCLUDE_REPACKTOOLS := true\n"
    )

    # The AOSP-based TWRP manifest puts the common product config in
    # vendor/twrp, while twrpdtgen's template targets the older omni manifest.
    if Path("vendor/twrp/config/common.mk").is_file():
        for product_mk in device_tree_path.glob("omni_*.mk"):
            product_mk.write_text(
                product_mk.read_text().replace(
                    "vendor/omni/config/common.mk",
                    "vendor/twrp/config/common.mk",
                )
            )

    recovery_fstab = device_tree_path / "recovery.fstab"
    lines = []
    for line in recovery_fstab.read_text().splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[0] == "/data":
            # Make data visible/wipeable in TWRP.  Decryption still needs the
            # vendor keymaster blobs; this only fixes the fstab metadata.
            fields[3] += ";wipeingui;mounttodecrypt;formattable;keydirectory=/metadata/vold/metadata_encryption"
            line = " ".join(fields)
        lines.append(line)
    recovery_fstab.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto-Twrp-Builder")
    parser.add_argument("-i", "--input", type=str, default="", help="Recovery/Boot Image")
    parser.add_argument("-o", "--output", type=str, default="", help="Output path")
    args = parser.parse_args()
    if not args.input or not args.output:
        parser.print_help()
        exit()
    if not os.path.exists(args.output):
        os.makedirs(args.output, exist_ok=True)

    device_tree = DeviceTree(Path(args.input))
    device_tree_path = device_tree.dump_to_folder(Path(args.output))
    _patch_device_tree(Path(device_tree_path))

    if os.getenv("GITHUB_OUTPUT", ""):
        with open(os.getenv("GITHUB_OUTPUT", ""), "w") as f:
            # DEVICE_NAME is used by the workflow's out/target/product/...
            # upload paths, so it must be the codename, not the manufacturer.
            f.write(f"DEVICE_NAME={device_tree.device_info.codename}\n")
            f.write(f"MAKEFILE_NAME=omni_{device_tree.device_info.codename}\n")
            f.write(
                f"DEVICE_PATH={os.path.basename(args.output) + os.sep + device_tree.device_info.manufacturer + os.sep + device_tree.device_info.codename}\n"
            )

    with open(
        os.path.join(
            args.output,
            device_tree.device_info.manufacturer,
            device_tree.device_info.codename,
            "Android.bp",
        ),
        "r",
    ) as f:
        print(f.read())
