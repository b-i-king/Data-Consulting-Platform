#!/usr/bin/env python3
"""
Package a generated data analysis skill into a distributable .skill file (zip format).

Usage:
    python package_data_skill.py <path/to/skill-folder> [output-directory]

Example:
    python package_data_skill.py /home/claude/acme-data-analyst
    python package_data_skill.py /home/claude/acme-data-analyst /tmp/outputs
"""

import sys
import zipfile
from pathlib import Path


def validate_skill(skill_path: Path) -> tuple[bool, str]:
    """
    Basic validation of skill structure.

    Checks, in order, and stops at the first problem:
    - SKILL.md exists in the folder
    - it starts with YAML frontmatter ("---")
    - "name:" appears in the first 500 characters and "description:" in the first 1,000
    - no unfilled placeholders such as "[PLACEHOLDER]" or "[COMPANY]" remain

    Args:
        skill_path: the skill folder to check.

    Returns:
        tuple[bool, str]: (True, "Validation passed") or (False, a message naming the problem).

    Example:
        >>> validate_skill(Path("/home/claude/acme-data-analyst"))
        (True, 'Validation passed')
    """

    # Check SKILL.md exists
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, "Missing SKILL.md"

    # Check SKILL.md has frontmatter
    content = skill_md.read_text()
    if not content.startswith("---"):
        return False, "SKILL.md missing YAML frontmatter"

    # Check for required frontmatter fields
    if "name:" not in content[:500]:
        return False, "SKILL.md missing 'name' in frontmatter"
    if "description:" not in content[:1000]:
        return False, "SKILL.md missing 'description' in frontmatter"

    # Check for placeholder text that wasn't filled in
    if "[PLACEHOLDER]" in content or "[COMPANY]" in content:
        return False, "SKILL.md contains unfilled placeholder text"

    return True, "Validation passed"


def package_skill(skill_path: str, output_dir: str = None) -> Path | None:
    """
    Package a skill folder into a .skill file.

    The folder is validated first (`validate_skill`), then every file in it is
    zipped, skipping hidden files/folders (names starting with ".") and common junk
    (__pycache__, .DS_Store, Thumbs.db). Paths inside the zip start with the folder
    name. Progress and errors are printed rather than raised. Note: the file written
    has a .zip extension, named after the folder.

    Args:
        skill_path: Path to the skill folder
        output_dir: Optional output directory (default: the current working directory;
            created if missing)

    Returns:
        Path to the created .skill file, or None if error

    Example:
        >>> package_skill("/home/claude/acme-data-analyst", "/tmp/outputs")
        PosixPath('/tmp/outputs/acme-data-analyst.zip')
    """
    skill_path = Path(skill_path).resolve()

    # Validate folder exists
    if not skill_path.exists():
        print(f"Error: Skill folder not found: {skill_path}")
        return None

    if not skill_path.is_dir():
        print(f"Error: Path is not a directory: {skill_path}")
        return None

    # Run validation
    print("Validating skill...")
    valid, message = validate_skill(skill_path)
    if not valid:
        print(f"Validation failed: {message}")
        return None
    print(f"{message}\n")

    # Determine output location
    skill_name = skill_path.name
    if output_dir:
        output_path = Path(output_dir).resolve()
    else:
        output_path = Path.cwd()

    output_path.mkdir(parents=True, exist_ok=True)
    skill_filename = output_path / f"{skill_name}.zip"

    # Create the zip file
    try:
        with zipfile.ZipFile(skill_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in skill_path.rglob('*'):
                if file_path.is_file():
                    # Skip hidden files and common junk
                    if any(part.startswith('.') for part in file_path.parts):
                        continue
                    if file_path.name in ['__pycache__', '.DS_Store', 'Thumbs.db']:
                        continue

                    # Calculate relative path within the zip
                    arcname = file_path.relative_to(skill_path.parent)
                    zipf.write(file_path, arcname)
                    print(f"  Added: {arcname}")

        print(f"\nSuccessfully packaged skill to: {skill_filename}")
        return skill_filename

    except Exception as e:
        print(f"Error creating zip file: {e}")
        return None


def main():
    """
    Command-line entry point: package the skill folder named in sys.argv.

    Usage: package_data_skill.py <path/to/skill-folder> [output-directory]. With no
    arguments it prints the usage text (the module docstring).

    Returns:
        None. Always ends the program with SystemExit: exit code 0 if the package was
        created, 1 on missing arguments or any error.

    Example:
        python package_data_skill.py /home/claude/acme-data-analyst /tmp/outputs
    """
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    skill_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"Packaging skill: {skill_path}")
    if output_dir:
        print(f"   Output directory: {output_dir}")
    print()

    result = package_skill(skill_path, output_dir)
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
