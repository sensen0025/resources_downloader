import subprocess
import os

def run_cmd(args):
    print("Running:", " ".join(args))
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        print("Error stdout:", r.stdout)
        print("Error stderr:", r.stderr)
        raise RuntimeError(f"Command failed with code {r.returncode}")
    print("Output:", r.stdout.strip())
    return r.stdout

try:
    # 1. Initialize git
    if not os.path.exists(".git"):
        run_cmd(["git", "init"])
    
    # 2. Configure local git user to avoid email/name prompt error
    run_cmd(["git", "config", "user.name", "sensen0025"])
    run_cmd(["git", "config", "user.email", "sensen0025@users.noreply.github.com"])
    
    # 3. Add files and commit
    run_cmd(["git", "add", "."])
    run_cmd(["git", "commit", "-m", "feat: upgrade AI-centric resource hub with streaming capture, adblock, and cookie bypass"])
    
    # 4. Set up branch and remote
    run_cmd(["git", "branch", "-M", "main"])
    
    # Remove existing remote if any
    try:
        run_cmd(["git", "remote", "remove", "origin"])
    except Exception:
        pass
    
    run_cmd(["git", "remote", "add", "origin", "git@github.com:sensen0025/resources_downloader.git"])
    
    # 5. Push using the Deploy Key (with StrictHostKeyChecking=no to prevent SSH prompt hang)
    ssh_cmd = "ssh -i github_deploy_key -o StrictHostKeyChecking=no"
    print("Pushing to GitHub origin main...")
    r = subprocess.run(
        ["git", "push", "-u", "origin", "main", "--force"],
        env={**os.environ, "GIT_SSH_COMMAND": ssh_cmd},
        capture_output=True,
        text=True
    )
    print("Push stdout:", r.stdout)
    print("Push stderr:", r.stderr)
    if r.returncode == 0:
        print("🎉 Code successfully pushed to GitHub!")
    else:
        print("❌ Push failed!")

except Exception as e:
    print("Failed to push:", e)
