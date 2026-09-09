const token = new URLSearchParams(window.location.hash.slice(1)).get("token");
history.replaceState(null, "", window.location.pathname);
const form = document.getElementById("reset");
const message = document.getElementById("message");
if (!token) {
  form.hidden = true;
  message.textContent = "The reset link is incomplete. Request a new one.";
}
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = document.getElementById("password").value;
  if (password !== document.getElementById("confirm").value) {
    message.textContent = "Passwords do not match.";
    return;
  }
  const button = form.querySelector("button");
  button.disabled = true;
  try {
    const response = await fetch("/api/v1/auth/password-reset/confirm", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token, password})
    });
    const data = await response.json();
    message.textContent = response.ok ? "Password updated. You can now log in." : data.error.message;
    if (response.ok) form.hidden = true;
  } catch {
    message.textContent = "Could not connect. Please try again.";
  } finally {
    button.disabled = false;
  }
});
