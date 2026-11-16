(() => {
    const form = document.getElementById("modalScriptExecArgumentsList");
    const script = document.getElementById("modalScriptExecScriptnameInput");
    if (!form || !script) return;

    // Older myMPD builds mistake accessible field descriptions for popovers.
    hidePopover = function (trigger) {
        let found = false;
        for (const element of document.querySelectorAll("[aria-describedby]")) {
            const popover = BSN.Popover.getInstance(element);
            if (popover) {
                found = true;
                if (element !== trigger) popover.hide();
            }
        }
        if (!found) document.querySelector(".popover")?.remove();
    };

    const descriptions = {
        provider: ["Music source", "Choose an installed service or enter a direct stream URL."],
        album_choice: ["Album", "Choose an album or navigate the album list."],
        item: ["Station or track", "Choose what starts when the current song finishes."],
        action: ["Listening action", "Choose an action, then submit to continue."],
        name: ["Name", "Enter a name for this session or mix."],
        url: ["Radio stream URL", "Enter a direct HTTP or HTTPS station stream URL."],
        minutes: ["Time budget in minutes", "Enter a whole number from 1 to 1440."],
        genre: ["Genre (optional)", "Leave blank to include all genres."],
        decade: ["Decade (optional)", "Enter a decade such as 1980, or leave blank."],
        mode: ["Mix mode", "Deep Shelf favors albums with fewer plays."],
        commit: ["Preview action", "Append or replace the queue, or reroll unlocked albums."]
    };

    function addActionTable() {
        const table = document.createElement("table");
        table.className = "table table-sm";
        table.dataset.listeningHelp = "";
        const header = table.createTHead().insertRow();
        for (const text of ["Option", "What it does"]) {
            const cell = document.createElement("th");
            cell.scope = "col";
            cell.textContent = text;
            header.append(cell);
        }
        const body = table.createTBody();
        for (const [option, detail] of [
            ["Save session", "Save the current queue, track, and playback position under a name."],
            ["Resume session", "Replace the current queue with a saved session and restore its playback position."],
            ["Radio handoff", "Let the current song finish, save your place, then play a station, iBroadcast selection, or direct stream."],
            ["Handoff status", "Check whether a handoff is waiting, complete, or failed."],
            ["Album mix builder", "Preview whole albums within a time budget. Use Deep Shelf to favor rarely played albums."]
        ]) {
            const row = body.insertRow();
            const label = document.createElement("th");
            label.scope = "row";
            label.textContent = option;
            row.append(label);
            row.insertCell().textContent = detail;
        }
        form.prepend(table);
    }

    function enhance() {
        if (script.value !== "Listening Tools" || form.querySelector("[data-listening-help]")) return;
        const controls = form.querySelectorAll("input:not([type=hidden]), select, button.chkBtn");
        if (!controls.length) return;
        if (form.querySelector('select[name="action"]')) {
            addActionTable();
        } else {
            const note = document.createElement("p");
            note.dataset.listeningHelp = "";
            if (form.querySelector('button[name^="keep_"]')) {
                note.textContent = "Checked albums stay in the mix when you reroll.";
            } else {
                note.hidden = true;
            }
            form.prepend(note);
        }
        for (const control of controls) {
            const row = control.closest(".form-group");
            const label = row?.querySelector("label");
            if (!label) continue;
            const id = "listening-field-" + control.name;
            control.id = id;
            label.htmlFor = id;
            let [name, hint] = descriptions[control.name] || [];
            if (control.name === "commit" && control.querySelector('option[value="cancel"]')) {
                [name, hint] = ["Confirm queue replacement", "Replace the current queue and play the mix, or cancel."];
            }
            if (control.name === "name" && control.tagName === "SELECT") {
                [name, hint] = ["Saved session", "Resuming replaces the current queue."];
            }
            if (control.name.startsWith("keep_")) {
                const index = control.name.slice(5);
                const album = form.querySelector('input[name="label_' + index + '"]')?.value;
                name = "Keep " + album;
                hint = "Keep this album when rerolling.";
                const update = () => control.setAttribute(
                    "aria-checked", String(control.classList.contains("active")));
                control.setAttribute("role", "checkbox");
                control.addEventListener("click", update);
                update();
            }
            if (!name) continue;
            label.textContent = name;
            control.title = hint;
            const help = document.createElement("small");
            help.id = id + "-help";
            help.className = "d-block form-text";
            help.textContent = hint;
            control.after(help);
            control.setAttribute("aria-describedby", help.id);
        }
    }

    new MutationObserver(enhance).observe(form, {childList: true});
})();
