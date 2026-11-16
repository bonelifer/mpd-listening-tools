-- {"name":"Listening Tools","file":"","version":0,"order":6,"arguments":[]}
local service = "http://127.0.0.1:18770"
local args = mympd_arguments

local function ask(title, fields)
    return mympd.dialog(title, fields, "Listening Tools")
end

local function field(name, value)
    return {name = name, type = "text", value = value or ""}
end

local function hidden(name, value)
    return {name = name, type = "hidden", value = value}
end

local function select(name, ids, labels)
    return {name = name, type = "select", value = ids,
        displayValue = labels or ids, defaultValue = ids[1]}
end

local function request(payload)
    local rc, status, _, body = mympd.http_client(
        "POST", service .. "/", "Content-Type: application/json\r\n",
        json.encode(payload), false
    )
    if rc == 0 and status == 200 then return json.decode(body) end
    return {message = "Listening Tools bridge unavailable"}
end

local function handoff_choices(provider, album, offset, name)
    local result = request({action = "choices", provider = provider,
        album = album, offset = offset or "0"})
    if not result.entries then return result.message end
    local ids, labels = {}, {}
    local field_name = provider == "ibroadcast" and album == "" and "album_choice" or "item"
    if provider == "ibroadcast" and album ~= "" and #result.entries > 0 then
        ids[1], labels[1] = "whole", "Play whole album"
    end
    for _, row in ipairs(result.entries) do
        ids[#ids + 1], labels[#labels + 1] = tostring(row.id), row.name
    end
    for _, direction in ipairs({"previous", "next"}) do
        if result[direction] then
            ids[#ids + 1] = "page:" .. result[direction]
            labels[#labels + 1] = direction == "previous" and "Previous page" or "Next page"
        end
    end
    if #ids == 0 then return "No handoff choices found" end
    return ask("Choose what to play after this song", {
        hidden("provider", provider), hidden("album", album),
        name and hidden("name", name) or field("name"),
        select(field_name, ids, labels), hidden("commit", "handoff-choice")
    })
end

if args.commit == "handoff-choice" then
    local choice = args.album_choice or args.item or ""
    local offset = choice:match("^page:(%d+)$")
    if offset then
        return handoff_choices(args.provider, args.album or "", offset, args.name)
    end
    if args.album_choice then
        return handoff_choices(args.provider, args.album_choice, "0", args.name)
    end
    args.commit = "handoff"
end

if args.provider and not args.commit then
    if args.provider == "direct" then
        return ask("Start radio after this song", {
            field("name"), field("url"), hidden("commit", "handoff")
        })
    end
    return handoff_choices(args.provider, "", "0")
end

if args.commit == "confirm-replace" then
    return ask("Replace the current queue and start this mix?", {
        hidden("name", args.name),
        select("commit", {"cancel", "replace"}, {"Cancel", "Replace queue and play"})
    })
elseif args.commit == "cancel" then
    return "Queue replacement canceled"
end

if args.commit then
    local locks = {}
    for key, value in pairs(args) do
        local index = key:match("^keep_(%d+)$")
        if index and value == "true" then
            locks[#locks + 1] = index
        end
    end
    table.sort(locks, function(a, b) return tonumber(a) < tonumber(b) end)
    local result = request({action = args.commit, name = args.name,
        url = args.url, provider = args.provider, item = args.item, album = args.album,
        minutes = args.minutes, decade = args.decade,
        genre = args.genre, locks = table.concat(locks, " "), mode = args.mode,
        unscrobbled = args.unscrobbled ~= "any" and args.unscrobbled or ""})
    if result.albums then
        local fields = {
            hidden("name", args.name), hidden("minutes", args.minutes),
            hidden("decade", args.decade), hidden("genre", args.genre),
            hidden("mode", args.mode), hidden("unscrobbled", args.unscrobbled or "")
        }
        local count = #locks
        for index, album in ipairs(result.albums) do
            fields[#fields + 1] = {
                name = "keep_" .. index, type = "checkbox",
                value = index <= count and "true" or "false"
            }
            fields[#fields + 1] = hidden("label_" .. index, album)
        end
        local tracks = args.unscrobbled == "tracks"
        fields[#fields + 1] = select("commit", {"queue", "confirm-replace", "mix"},
            {"Append this mix", "Replace queue with this mix",
             tracks and "Reroll unlocked tracks" or "Reroll unlocked albums"})
        return ask((tracks and "Track mix (" or "Album mix (") .. result.minutes .. " min)",
            fields)
    end
    return result.message
end

if args.action == "save" then
    return ask("Save the current session", {field("name"), hidden("commit", "save")})
elseif args.action == "resume" then
    local result = request({action = "sessions"})
    if not result.sessions then return result.message end
    if #result.sessions == 0 then return "No saved sessions" end
    return ask("Replace the current queue with a saved session?", {
        select("name", result.sessions), hidden("commit", "resume")
    })
elseif args.action == "handoff" then
    local ids, labels, installed = {"direct"}, {"Direct stream URL"}, {}
    local rc, response = mympd.api("MYMPD_API_SCRIPT_LIST", {all = true})
    if rc == 0 then
        for _, row in ipairs(response.data or {}) do installed[#installed + 1] = row.name end
    end
    local sources = request({action = "sources", installed = installed})
    for _, row in ipairs(sources.entries or {}) do
        ids[#ids + 1], labels[#labels + 1] = row.id, row.name
    end
    return ask("Choose a handoff source", {select("provider", ids, labels)})
elseif args.action == "handoff-status" then
    return request({action = "handoff-status"}).message
elseif args.action == "mix" then
    return ask("Build an album mix", {
        field("name"), field("minutes", "120"), field("genre"), field("decade"),
        select("mode", {"regular", "deep"}, {"Fit the time budget", "Deep Shelf"}),
        select("unscrobbled", {"any", "albums", "tracks"},
            {"Any albums", "Only never-scrobbled albums", "Only never-scrobbled tracks"}),
        hidden("commit", "mix")
    })
elseif args.action == "scrobbles" then
    return ask("Last.fm history", {select("commit", {"scrobble-status", "scrobble-sync"},
        {"Show what is saved", "Download new scrobbles"})})
end

return ask("Listening Tools", {select("action",
    {"save", "resume", "handoff", "handoff-status", "mix", "scrobbles"},
    {"Save session", "Resume session", "Radio handoff", "Handoff status", "Album mix builder",
     "Last.fm history"})})
