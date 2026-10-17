"""
Sarvam AI TTS generation for the dice-skit audio pipeline.

    *** THIS STAGE IS CURRENTLY DISABLED. ***

Every line that calls Sarvam, loads an API key, or writes a dialogue MP3 is
commented out below. Nothing in this module makes a network request, reads
.env, or costs money in its current state. Running it prints a notice and
exits 0.

Why it is disabled
------------------
The dialogue MP3s in in-out/machine_gen/ are treated as already generated. Re-
stitching them should not require a Sarvam API key, a network round-trip, or
paying to re-synthesise every row. Stitching now lives in stitch_mp3.py and
runs entirely independently of this file.

How to re-enable it
-------------------
1. Install the dependencies:

       pip install sarvamai python-dotenv

2. Create a .env file next to this script containing:

       SARVAM_API_KEY=your_actual_api_key

3. Uncomment the four blocks below, each marked with a
   "--- UNCOMMENT TO ENABLE ---" banner.

4. Set "tts": {"enabled": true} in config.json.

5. Run:

       python generate_mp3.py           # all rows
       python generate_mp3.py 100       # first 100 rows only

Then run `python stitch_mp3.py` to build the master.

The commented code is kept in a state where uncommenting is the only step
required: it already calls the shared helpers in audio_common.py rather than
the private copies the original single-file script used.
"""

import sys

import audio_common as common


# Master switch: tts.enabled in config.json. Turn it on only after
# uncommenting the blocks below.
TTS_ENABLED = common.TTS_ENABLED


# ============================================================
# --- UNCOMMENT TO ENABLE --- (1 of 4)  imports
# ============================================================
#
# import base64
# from pathlib import Path
#
# from dotenv import load_dotenv
# from sarvamai import SarvamAI
#
# import os


# ============================================================
# --- UNCOMMENT TO ENABLE --- (2 of 4)  Sarvam client
# ============================================================
#
# # Load .env from the same directory as this script.
# ENV_FILE = common.BASE_DIRECTORY / ".env"
# load_dotenv(ENV_FILE)
#
# # The key is never in config.json; tts.api_key_source says where it is.
# if common.TTS_API_KEY_SOURCE == "keyring":
#     import keyring
#     API_KEY = keyring.get_password("dice-skit", "SARVAM_API_KEY")
# else:
#     API_KEY = os.getenv("SARVAM_API_KEY")
#
# if not API_KEY:
#     print()
#     print("ERROR: SARVAM_API_KEY was not found.")
#     print()
#     print("Expected .env file:")
#     print(f"    {ENV_FILE}")
#     print()
#     print("Your .env should contain:")
#     print("    SARVAM_API_KEY=your_actual_api_key")
#     print()
#     sys.exit(1)
#
# print("Sarvam API key loaded successfully.")
#
# client = SarvamAI(
#     api_subscription_key=API_KEY
# )


# ============================================================
# --- UNCOMMENT TO ENABLE --- (3 of 4)  single-file TTS
# ============================================================
#
# def generate_tts(text, speaker_name, output_file):
#     """
#     Generate a single MP3 using Sarvam TTS.
#
#     This uses the Sarvam Python SDK.
#     """
#
#     print()
#     print("Generating TTS:")
#     print(f"  Speaker : {speaker_name}")
#     print(f"  Output  : {output_file}")
#
#     try:
#         response = client.text_to_speech.convert(
#             text=text,
#             language_code=common.LANGUAGE_CODE,
#             model=common.MODEL,
#             speaker=speaker_name,
#             pace=common.PACE,
#             speech_sample_rate=common.SPEECH_SAMPLE_RATE,
#             output_audio_codec=common.OUTPUT_AUDIO_CODEC,
#         )
#
#     except Exception as exc:
#         print()
#         print("SARVAM TTS ERROR:")
#         print(exc)
#         raise
#
#     # Sarvam returns base64 encoded audio.
#     if not hasattr(response, "audios"):
#         raise RuntimeError(
#             "Sarvam response does not contain 'audios'. "
#             f"Response: {response}"
#         )
#
#     if not response.audios:
#         raise RuntimeError(
#             "Sarvam returned an empty audio response."
#         )
#
#     audio_data = response.audios[0]
#
#     try:
#         audio_bytes = base64.b64decode(audio_data)
#     except Exception as exc:
#         raise RuntimeError(
#             f"Could not decode Sarvam audio response: {exc}"
#         )
#
#     temp_file = output_file.with_name(
#         output_file.stem + "_tts_tmp.mp3"
#     )
#
#     if temp_file.exists():
#         temp_file.unlink()
#
#     with open(temp_file, "wb") as f:
#         f.write(audio_bytes)
#
#     # IMPORTANT:
#     # Do not accept a file just because it exists.
#     # Validate it before moving it into the final location.
#     if not common.is_valid_audio(temp_file):
#         if temp_file.exists():
#             temp_file.unlink()
#
#         raise RuntimeError(
#             f"Sarvam generated an invalid MP3: {output_file}"
#         )
#
#     if output_file.exists():
#         output_file.unlink()
#
#     temp_file.replace(output_file)
#
#     duration = common.get_audio_duration(output_file)
#
#     if duration is not None:
#         print(
#             f"Created: {output_file.name} "
#             f"({duration:.2f} sec)"
#         )
#     else:
#         print(f"Created: {output_file.name}")


# ============================================================
# --- UNCOMMENT TO ENABLE --- (4 of 4)  batch generation
# ============================================================
#
# def prepare_dialogue_files(records):
#     """
#     Generate all dialogue MP3s.
#
#     Existing valid files are reused when
#     common.OVERWRITE_EXISTING is False.
#
#     Existing corrupt files are ALWAYS regenerated.
#     """
#
#     dialogue_files = []
#
#     total = len(records)
#
#     print()
#     print("=" * 70)
#     print("PREPARING DIALOGUE MP3 FILES")
#     print("=" * 70)
#     print(f"Total dialogue rows: {total}")
#
#     for index, record in enumerate(records, start=1):
#
#         dialogue_text = record["dialogue_text"]
#         speaker_name = record["speaker"]
#
#         output_file = common.dialogue_path(record)
#
#         print()
#         print(f"[{index}/{total}] {output_file.name}")
#
#         if not speaker_name:
#             raise RuntimeError(
#                 f"Row {record['row_number']} has no speaker. "
#                 "Sarvam TTS requires a speaker name; add a "
#                 "'speaker' or 'speaker_name' column to the CSV."
#             )
#
#         # ----------------------------------------------------
#         # Existing file
#         # ----------------------------------------------------
#
#         if output_file.exists():
#
#             valid = common.is_valid_audio(output_file)
#
#             if valid and not common.OVERWRITE_EXISTING:
#
#                 print(
#                     f"VALID - skipping existing file: "
#                     f"{output_file.name}"
#                 )
#
#                 dialogue_files.append(output_file)
#
#                 continue
#
#             if not valid:
#
#                 print(
#                     f"CORRUPT - regenerating: "
#                     f"{output_file.name}"
#                 )
#
#                 output_file.unlink()
#
#             elif common.OVERWRITE_EXISTING:
#
#                 print(
#                     f"OVERWRITE enabled - regenerating: "
#                     f"{output_file.name}"
#                 )
#
#                 output_file.unlink()
#
#         # ----------------------------------------------------
#         # Generate
#         # ----------------------------------------------------
#
#         generate_tts(
#             text=dialogue_text,
#             speaker_name=speaker_name,
#             output_file=output_file,
#         )
#
#         # Final safety validation
#         if not common.is_valid_audio(output_file):
#             raise RuntimeError(
#                 f"Generated MP3 failed validation: {output_file}"
#             )
#
#         dialogue_files.append(output_file)
#
#     return dialogue_files


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print("SARVAM DIALOGUE MP3 GENERATOR")
    print("=" * 70)

    if not TTS_ENABLED:
        print()
        print("TTS generation is DISABLED.")
        print()
        print(
            "This stage is intentionally switched off: the dialogue MP3s in"
        )
        print(
            f"{common.OUT_DIRECTORY} are assumed to be already generated."
        )
        print()
        print("To enable generation:")
        print("    1. pip install sarvamai python-dotenv")
        print(f"    2. create {common.BASE_DIRECTORY / '.env'} with")
        print("       SARVAM_API_KEY=your_actual_api_key")
        print("    3. uncomment the four marked blocks in generate_mp3.py")
        print('    4. set "tts": {"enabled": true} in config.json')
        print()
        print("To stitch the existing MP3s instead:")
        print("    python stitch_mp3.py")
        print()

        return 0

    # --------------------------------------------------------
    # --- UNCOMMENT TO ENABLE --- live generation path
    # --------------------------------------------------------
    #
    # row_limit = common.parse_arguments(sys.argv).row_limit
    #
    # common.OUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    #
    # print()
    # print(f"Loading CSV: {common.CSV_FILE}")
    #
    # records = common.load_csv(row_limit=row_limit)
    #
    # if row_limit is not None:
    #     print(f"Row limit enabled: {row_limit}")
    #
    # print(f"Dialogue records loaded: {len(records)}")
    #
    # if not records:
    #     print("ERROR: No dialogue records found.")
    #     return 1
    #
    # dialogue_files = prepare_dialogue_files(records)
    #
    # print()
    # print("=" * 70)
    # print("GENERATION COMPLETE")
    # print("=" * 70)
    # print(f"Dialogue MP3 files: {len(dialogue_files)}")
    # print()
    # print("Next step:")
    # print("    python stitch_mp3.py")
    # print()
    #
    # return 0

    print()
    print(
        "ERROR: TTS_ENABLED is True but the generation blocks are still "
        "commented out."
    )
    print("Uncomment the four marked blocks in generate_mp3.py.")
    print()

    return 1


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        sys.exit(main())

    except KeyboardInterrupt:
        print()
        print("Process interrupted by user.")
        sys.exit(1)

    except Exception as exc:
        print()
        print("=" * 70)
        print("ERROR")
        print("=" * 70)
        print(exc)
        print()
        sys.exit(1)