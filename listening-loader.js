(() => {
    const script = document.createElement("script");
    const url = new URL("browse/pics/listening-accessibility.js?v=5", document.currentScript.src).href;
    const policy = window.trustedTypes?.createPolicy("listening-tools-ui", {
        createScriptURL(candidate) {
            if (candidate !== url) {
                throw new TypeError("Unexpected Listening Tools script URL");
            }
            return url;
        }
    });
    script.src = policy ? policy.createScriptURL(url) : url;
    document.head.append(script);
})();
