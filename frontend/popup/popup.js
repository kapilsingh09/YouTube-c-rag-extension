// Generate a unique session ID for the backend conversation
function generateSessionId() {
    return 'sess_' + Math.random().toString(36).substring(2, 15);
}

// Configuration
const API_BASE_URL = "http://localhost:8000";
const WELCOME_GREETINGS = [
    'Hello there! ✨',
    'Ready when you are 🚀',
    "Let's make today count 🌱",
    'Curious? Let’s explore 🔎',
    'You’ve got this.. 💪',
    'Let’s dive in. 🔎',
    'Big ideas start here..💡',
    'One question at a time 🌿',
    'Let’s make it count ⚡',
    'Bring your curiosity 🌟'
];

// State
let currentVideoInfo = null;
let sessionId = null;
let isBannerVisible = true;
let isModelPillVisible = true;
let currentActiveBotMessageDiv = null;
let isSending = false;
let streamFinished = false;
let currentRequestId = null;
let activeWelcomeGreeting = null;
let currentWebSearchUsed = false;
let currentRagUsed = false;
let currentSourceType = null;

// DOM Elements
const videoTitleEl = document.getElementById('video-title');
const videoThumbnailEl = document.getElementById('video-thumbnail');
const chatHistoryEl = document.getElementById('chat-history');
const inputWrapper = document.getElementById('input-wrapper');
const questionInput = document.getElementById('question-input');
const askBtn = document.getElementById('ask-btn');
const resetBtn = document.getElementById('reset-btn');
const closeSettingsBtn = document.getElementById('close-settings-btn');

const modelSelect = document.getElementById('model-select');
const saveSetupBtn = document.getElementById('save-setup-btn');

const setupContainer = document.getElementById('setup-container');
const activeModelText = document.getElementById('active-model-text');
const modelPillContainer = document.getElementById('model-pill-container');
const modelPill = document.getElementById('model-pill');

// Toggle Elements
const toggleBannerBtn = document.getElementById('toggle-banner-btn');
const toggleBannerIcon = document.getElementById('toggle-banner-icon');
const videoBanner = document.getElementById('video-banner');

const toggleModelBtn = document.getElementById('toggle-model-btn');
const toggleModelIcon = document.getElementById('toggle-model-icon');

// Initialize
document.addEventListener('DOMContentLoaded', async () => {
    // 1. Get the current active tab
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const tab = tabs[0];
    
    if (tab && tab.url && tab.url.includes('youtube.com/watch')) {
        const urlParams = new URL(tab.url).searchParams;
        const videoId = urlParams.get('v');
        
        if (videoId) {
            currentVideoInfo = {
                url: tab.url,
                videoId: videoId,
                title: tab.title.replace(/^\(\d+\)\s*/, '').replace(' - YouTube', '')
            };
            
            videoTitleEl.textContent = currentVideoInfo.title;
            videoTitleEl.title = currentVideoInfo.title;
            
            videoThumbnailEl.src = `https://img.youtube.com/vi/${videoId}/mqdefault.jpg`;
            videoThumbnailEl.style.display = 'block';
            
            questionInput.disabled = false;
            updateSendButtonState();

            loadState(videoId);

        } else {
            videoTitleEl.textContent = "Status: Invalid YouTube URL";
            renderWelcomeState(false);
        }
    } else {
        videoTitleEl.textContent = "Status: Not on a YouTube video";
        renderWelcomeState(false);
    }
});

// Load state from storage
function loadState(videoId) {
    chrome.storage.local.get([
        'apiKey', 'selectedModel', 'videoId', 'sessionId', 
        'chatHistory', 'isBannerVisible', 'isModelPillVisible',
        'isGenerating', 'currentStream', 'currentWebSearchUsed', 'currentRagUsed', 'currentSourceType', 'currentError', 'currentRequestId'
    ], (result) => {
        currentWebSearchUsed = Boolean(result.currentWebSearchUsed);
        currentRagUsed = Boolean(result.currentRagUsed);
        currentSourceType = result.currentSourceType || null;
        
        // Restore Toggles
        if (result.isBannerVisible !== undefined) {
            isBannerVisible = result.isBannerVisible;
            updateBannerVisibility();
        }
        if (result.isModelPillVisible !== undefined) {
            isModelPillVisible = result.isModelPillVisible;
            updateModelPillVisibility();
        }

        // Restore selected model only
        if (['gemini', 'groq'].includes(result.selectedModel)) {
            modelSelect.value = result.selectedModel;
        } else {
            modelSelect.value = 'groq';
        }
        if (modelSelect.value) {
            updateActiveModelText();
        }

        updateSettingsUI();

        // Restore chat history if video matches
        if (result.videoId === videoId && result.sessionId) {
            sessionId = result.sessionId;
            currentRequestId = result.currentRequestId || null;
            const history = (result.chatHistory || []).filter((message, index) => !(
                index === 0 &&
                message.sender === 'bot' &&
                message.text === "Hello! I'm ready to answer questions about this video. What would you like to know?"
            ));
            if (history.length !== (result.chatHistory || []).length) {
                chrome.storage.local.set({ chatHistory: history });
            }
            renderChatHistory(history);
            
            // Resume stream if currently generating
            if (result.isGenerating) {
                setLoadingState(true);
                if (result.currentStream) {
                    // Update existing stream
                    currentActiveBotMessageDiv = document.createElement('div');
                    currentActiveBotMessageDiv.classList.add('message', 'bot-message');
                    renderBotAnswer(
                        currentActiveBotMessageDiv,
                        result.currentStream,
                        currentWebSearchUsed,
                        currentRagUsed
                    );
                    chatHistoryEl.appendChild(currentActiveBotMessageDiv);
                    scrollToBottom();
                } else {
                    // Start thinking indicator
                    currentActiveBotMessageDiv = addLoadingIndicator();
                }
            } else if (result.currentError) {
                renderMessage("bot", result.currentError, true);
                chrome.storage.local.set({ currentError: null }); // clear after showing
            }

        } else {
            // New video or no history, start fresh
            sessionId = generateSessionId();
            chrome.storage.local.set({ 
                videoId: videoId, 
                sessionId: sessionId, 
                chatHistory: [],
                isGenerating: false,
                currentStream: "",
                currentWebSearchUsed: false,
                currentRagUsed: false,
                currentRequestId: null,
                currentError: null
            });
            renderWelcomeState(true);
        }
    });
}

function updateActiveModelText() {
    activeModelText.textContent = modelSelect.value === 'gemini' ? 'Gemini' : 'Groq';
}

function updateSettingsUI() {
    // API keys are configured in the backend only; no field is shown in the popup.
}

function showSetupView() {
    setupContainer.classList.remove('hidden');
    setupContainer.classList.add('open');
    setupContainer.classList.add('flex');
}

function hideSetupView() {
    setupContainer.classList.add('hidden');
    setupContainer.classList.remove('open');
    setupContainer.classList.remove('flex');
}

// --- Toggle Logic ---

function updateBannerVisibility() {
    if (isBannerVisible) {
        videoBanner.classList.remove('hidden-banner');
        toggleBannerIcon.classList.remove('fa-chevron-down');
        toggleBannerIcon.classList.add('fa-chevron-up');
    } else {
        videoBanner.classList.add('hidden-banner');
        toggleBannerIcon.classList.remove('fa-chevron-up');
        toggleBannerIcon.classList.add('fa-chevron-down');
    }
}

toggleBannerBtn.addEventListener('click', () => {
    isBannerVisible = !isBannerVisible;
    chrome.storage.local.set({ isBannerVisible });
    updateBannerVisibility();
});

function updateModelPillVisibility() {
    if (isModelPillVisible) {
        modelPill.classList.remove('opacity-0', 'scale-90', 'absolute');
        modelPill.classList.add('opacity-100', 'scale-100');
        setTimeout(() => { if (isModelPillVisible) modelPill.style.visibility = 'visible'; }, 300);
        toggleModelIcon.classList.remove('fa-eye');
        toggleModelIcon.classList.add('fa-eye-slash');
    } else {
        modelPill.classList.remove('opacity-100', 'scale-100');
        modelPill.classList.add('opacity-0', 'scale-90', 'absolute');
        setTimeout(() => { if (!isModelPillVisible) modelPill.style.visibility = 'hidden'; }, 300);
        toggleModelIcon.classList.remove('fa-eye-slash');
        toggleModelIcon.classList.add('fa-eye');
    }
}

toggleModelBtn.addEventListener('click', () => {
    isModelPillVisible = !isModelPillVisible;
    chrome.storage.local.set({ isModelPillVisible });
    updateModelPillVisibility();
});

// --- Event Listeners ---

modelPillContainer.addEventListener('click', (e) => {
    if (e.target.closest('#toggle-model-btn')) return;
    if (setupContainer.classList.contains('hidden')) {
        showSetupView();
    } else {
        hideSetupView();
    }
});
modelPillContainer.style.cursor = 'pointer';
closeSettingsBtn.addEventListener('click', () => hideSetupView());

askBtn.addEventListener('click', () => {
    if (isSending) {
        cancelGeneration();
    } else {
        handleAskQuestion();
    }
});
questionInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (!isSending) handleAskQuestion();
    }
});
questionInput.addEventListener('input', () => {
    resizeQuestionInput();
    updateSendButtonState();
});

resetBtn.addEventListener('click', () => {
    sessionId = generateSessionId();
    chrome.storage.local.set({ 
        sessionId: sessionId, 
        chatHistory: [],
        isGenerating: false,
        currentStream: "",
        currentWebSearchUsed: false,
        currentRagUsed: false,
        currentRequestId: null,
        currentError: null
    });
    renderWelcomeState(Boolean(currentVideoInfo));
});

modelSelect.addEventListener('change', updateSettingsUI);

saveSetupBtn.addEventListener('click', () => {
    const model = modelSelect.value;

    chrome.storage.local.set({
        selectedModel: model
    });

    updateActiveModelText();
    hideSetupView();
});


// --- Live Storage Updates (The Magic) ---

chrome.storage.onChanged.addListener((changes, namespace) => {
    if (namespace !== 'local') return;

    if (changes.currentWebSearchUsed) {
        currentWebSearchUsed = Boolean(changes.currentWebSearchUsed.newValue);
    }
    if (changes.currentRagUsed) {
        currentRagUsed = Boolean(changes.currentRagUsed.newValue);
    }
    if (changes.currentSourceType) {
        currentSourceType = changes.currentSourceType.newValue || null;
    }

    // Chat History updated (either user asked a question, or bot finished)
    if (changes.chatHistory) {
        const history = changes.chatHistory.newValue || [];
        const lastMessage = history[history.length - 1];

        // The final answer is already visible in the live stream bubble.
        // Update that bubble instead of rendering a second copy from history.
        if (
            lastMessage?.sender === 'bot' &&
            currentActiveBotMessageDiv &&
            !currentActiveBotMessageDiv.classList.contains('skeleton-message')
        ) {
            renderBotAnswer(
                currentActiveBotMessageDiv,
                lastMessage.text,
                Boolean(lastMessage.webSearchUsed),
                Boolean(lastMessage.ragUsed),
                lastMessage.sourceType || currentSourceType
            );
            currentActiveBotMessageDiv = null;
            streamFinished = true;
            scrollToBottom();
        } else {
            renderChatHistory(history);
        }
    }

    // Streaming updates
    if (changes.currentStream && !streamFinished) {
        const streamText = changes.currentStream.newValue;
        if (streamText) {
            // Replace thinking indicator with actual text bubble if it's the first chunk
            if (!currentActiveBotMessageDiv || currentActiveBotMessageDiv.classList.contains('skeleton-message')) {
                if (currentActiveBotMessageDiv) currentActiveBotMessageDiv.remove();
                currentActiveBotMessageDiv = document.createElement('div');
                currentActiveBotMessageDiv.classList.add('message', 'bot-message');
                chatHistoryEl.appendChild(currentActiveBotMessageDiv);
            }
            renderBotAnswer(currentActiveBotMessageDiv, streamText, currentWebSearchUsed, currentRagUsed, currentSourceType);
            scrollToBottom();
        }
    }

    // Is Generating State Changes
    if (changes.isGenerating) {
        const isGen = changes.isGenerating.newValue;
        setLoadingState(isGen);
        
        if (isGen) {
            // Just started, show thinking indicator
            streamFinished = false;
            if (currentActiveBotMessageDiv) currentActiveBotMessageDiv.remove();
            currentActiveBotMessageDiv = addLoadingIndicator();
        } else {
            // Finished generating
            streamFinished = true;
            if (currentActiveBotMessageDiv) currentActiveBotMessageDiv.remove();
            currentActiveBotMessageDiv = null;
        }
    }

    // Errors
    if (changes.currentError && changes.currentError.newValue) {
        renderMessage("bot", changes.currentError.newValue, true);
        chrome.storage.local.set({ currentError: null }); // clear it so it doesn't fire again on reload
    }
});


// --- Ask Question Logic ---

function handleAskQuestion() {
    const question = questionInput.value.trim();

    if (!question || !currentVideoInfo || isSending) return;

    clearWelcomeState();
    currentRequestId = generateSessionId();
    setLoadingState(true);
    questionInput.placeholder = 'Waiting for response…';
    questionInput.value = '';
    resizeQuestionInput();
    updateSendButtonState();

    if (currentActiveBotMessageDiv) currentActiveBotMessageDiv.remove();
    currentActiveBotMessageDiv = addLoadingIndicator();

    chrome.runtime.sendMessage({
        type: "ASK_QUESTION",
        payload: {
            youtube_url: currentVideoInfo.url,
            video_title: currentVideoInfo.title,
            question: question,
            session_id: sessionId,
            model: modelSelect.value,
            requestId: currentRequestId
        }
    });
}

function cancelGeneration() {
    if (!currentRequestId) return;
    askBtn.disabled = true;
    askBtn.setAttribute('aria-label', 'Stopping generation');
    askBtn.title = 'Stopping generation';
    chrome.runtime.sendMessage({
        type: 'CANCEL_GENERATION',
        requestId: currentRequestId
    });
}

// --- DOM Render Functions ---

function renderMessage(sender, text, isError = false, webSearchUsed = false, ragUsed = false, sourceType = null) {
    clearWelcomeState();

    const msgDiv = document.createElement('div');
    if (isError) {
        msgDiv.classList.add('message', 'error-message');
    } else {
        msgDiv.classList.add('message', sender === 'user' ? 'user-message' : 'bot-message');
    }
    if (sender === 'bot' && !isError) {
        renderBotAnswer(msgDiv, text, webSearchUsed, ragUsed, sourceType);
    } else {
        msgDiv.textContent = text;
    }
    chatHistoryEl.appendChild(msgDiv);
    scrollToBottom();
}

function normalizeSourceType(sourceType) {
    const normalized = String(sourceType || '').trim().toLowerCase().replace(/[+\s-]+/g, '_');
    if (['rag_web', 'web_rag', 'rag_and_web', 'both', 'combined'].includes(normalized)) return 'rag_web';
    if (normalized === 'web') return 'web';
    if (normalized === 'rag') return 'rag';
    return null;
}

function resolveSourceType(sourceType, webSearchUsed, ragUsed) {
    const metadataSourceType = normalizeSourceType(sourceType);
    const effectiveWebUsed = Boolean(webSearchUsed) || metadataSourceType === 'web' || metadataSourceType === 'rag_web';
    const effectiveRagUsed = Boolean(ragUsed) || metadataSourceType === 'rag' || metadataSourceType === 'rag_web';

    if (effectiveRagUsed && effectiveWebUsed) return 'rag_web';
    if (effectiveWebUsed) return 'web';
    if (effectiveRagUsed) return 'rag';
    return null;
}

function renderBotAnswer(container, text, webSearchUsed, ragUsed, sourceType = null) {
    const resolvedText = String(text || '');
    const normalizedSourceType = resolveSourceType(sourceType, webSearchUsed, ragUsed);

    container.replaceChildren();

    if (normalizedSourceType === 'rag_web') {
        const topBadge = document.createElement('div');
        topBadge.className = 'top-source-badge';
        const badgeText = document.createElement('span');
        badgeText.textContent = '📚 RAG + 🌐 WEB';
        topBadge.appendChild(badgeText);
        container.appendChild(topBadge);
    } else if (normalizedSourceType === 'web') {
        const topBadge = document.createElement('div');
        topBadge.className = 'top-source-badge';
        const badgeText = document.createElement('span');
        badgeText.textContent = '🌐 WEB';
        topBadge.appendChild(badgeText);
        container.appendChild(topBadge);
    } else if (normalizedSourceType === 'rag') {
        const topBadge = document.createElement('div');
        topBadge.className = 'top-source-badge';
        const badgeText = document.createElement('span');
        badgeText.textContent = '📚 RAG';
        topBadge.appendChild(badgeText);
        container.appendChild(topBadge);
    }

    const contentDiv = document.createElement('div');
    contentDiv.className = 'markdown-content';
    const visibleText = resolvedText
        .replaceAll('\u001eWEB_SEARCH_USED\u001e', '')
        .replaceAll('\u001eRAG_USED\u001e', '')
        .replaceAll(/\u001eSOURCE_TYPE:[^\u001e]*\u001e/g, '');
    renderMarkdownInto(contentDiv, visibleText);
    container.appendChild(contentDiv);
}

function renderChatHistory(historyArr) {
    chatHistoryEl.innerHTML = '';
    if (!historyArr) return;
    historyArr.forEach(msg => {
        const msgText = String(msg.text || '');
        const webUsed = Boolean(msg.webSearchUsed) || msgText.includes('\u001eWEB_SEARCH_USED\u001e');
        const ragUsed = Boolean(msg.ragUsed) || msgText.includes('\u001eRAG_USED\u001e');
        const msgSourceType = msg.sourceType || msg.source_type || null;
        renderMessage(msg.sender, msg.text, false, webUsed, ragUsed, msgSourceType);
    });

    if (!historyArr.length && !currentActiveBotMessageDiv) {
        renderWelcomeState(Boolean(currentVideoInfo));
    }

    if (currentActiveBotMessageDiv) {
        chatHistoryEl.appendChild(currentActiveBotMessageDiv);
        scrollToBottom();
    }
}

function renderWelcomeState(hasVideo) {
    chatHistoryEl.replaceChildren();

    const welcome = document.createElement('div');
    welcome.className = 'empty-state';

    const heading = document.createElement('h2');
    if (hasVideo && !activeWelcomeGreeting) {
        activeWelcomeGreeting = WELCOME_GREETINGS[Math.floor(Math.random() * WELCOME_GREETINGS.length)];
    }
    heading.textContent = hasVideo ? activeWelcomeGreeting : 'Open a YouTube video to get started';

    welcome.appendChild(heading);
    if (!hasVideo) {
        const description = document.createElement('p');
        description.textContent = 'Then ask me anything about it.';
        welcome.appendChild(description);
    }
    chatHistoryEl.append(welcome, inputWrapper);
    document.body.classList.add('welcome-mode');
}

function clearWelcomeState() {
    const welcome = chatHistoryEl.querySelector('.empty-state');
    if (!welcome) return;

    welcome.remove();
    document.body.classList.remove('welcome-mode');
    document.body.insertBefore(inputWrapper, document.querySelector('script'));
    activeWelcomeGreeting = null;
}

function addLoadingIndicator() {
    const skeleton = document.createElement('div');
    skeleton.classList.add('skeleton-message');

    const line1 = document.createElement('div');
    line1.classList.add('skeleton-line', 'long');
    const line2 = document.createElement('div');
    line2.classList.add('skeleton-line', 'medium');
    const line3 = document.createElement('div');
    line3.classList.add('skeleton-line', 'short');

    skeleton.appendChild(line1);
    skeleton.appendChild(line2);
    skeleton.appendChild(line3);

    chatHistoryEl.appendChild(skeleton);
    scrollToBottom();
    return skeleton;
}

function setLoadingState(isLoading) {
    isSending = isLoading;
    askBtn.classList.toggle('is-generating', isLoading);
    const icon = askBtn.querySelector('i');
    icon.className = isLoading ? 'fa-solid fa-circle-notch' : 'fa-solid fa-arrow-up text-[13px]';
    askBtn.setAttribute('aria-label', isLoading ? 'Stop generating' : 'Send message');
    askBtn.title = isLoading ? 'Stop generating' : 'Send message';
    askBtn.setAttribute('aria-busy', String(isLoading));
    questionInput.placeholder = isLoading ? 'Waiting for response…' : 'Ask about this video…';
    updateSendButtonState();
    if (!isLoading) {
        currentRequestId = null;
        questionInput.focus();
    }
}

function updateSendButtonState() {
    if (isSending) {
        askBtn.disabled = false;
        return;
    }
    askBtn.disabled = !questionInput.value.trim();
}

function resizeQuestionInput() {
    questionInput.style.height = 'auto';
    questionInput.style.height = Math.min(questionInput.scrollHeight, 96) + 'px';
}

function renderMarkdownInto(container, markdown) {
    container.replaceChildren();
    const lines = String(markdown || '').replace(/\r\n?/g, '\n').split('\n');
    renderMarkdownBlocks(container, lines);
}

function renderMarkdownBlocks(parent, lines) {
    let index = 0;
    while (index < lines.length) {
        const line = lines[index];
        const trimmed = line.trim();
        if (!trimmed) {
            index++;
            continue;
        }

        const fence = trimmed.match(/^(```+|~~~+)(.*)$/);
        if (fence) {
            const codeLines = [];
            const closingFence = fence[1][0];
            const fenceLength = fence[1].length;
            index++;
            while (index < lines.length && !new RegExp('^\\s*' + closingFence + '{' + fenceLength + ',}\\s*$').test(lines[index])) {
                codeLines.push(lines[index++]);
            }
            if (index < lines.length) index++;
            appendCodeBlock(parent, codeLines.join('\n'), fence[2].trim().split(/\s+/)[0] || 'text');
            continue;
        }

        const heading = trimmed.match(/^(#{1,6})\s+(.+?)\s*#*$/);
        if (heading) {
            const element = document.createElement('h' + heading[1].length);
            appendInlineMarkdown(element, heading[2]);
            parent.appendChild(element);
            index++;
            continue;
        }

        if (/^(---+|___+|\*\*\*+)\s*$/.test(trimmed)) {
            parent.appendChild(document.createElement('hr'));
            index++;
            continue;
        }

        if (/^>\s?/.test(trimmed)) {
            const quoteLines = [];
            while (index < lines.length && /^\s*>/.test(lines[index])) {
                quoteLines.push(lines[index++].replace(/^\s*>\s?/, ''));
            }
            const quote = document.createElement('blockquote');
            renderMarkdownBlocks(quote, quoteLines);
            parent.appendChild(quote);
            continue;
        }

        const listMatch = trimmed.match(/^([-+*]|\d+[.)])\s+(.+)$/);
        if (listMatch) {
            const ordered = /^\d/.test(listMatch[1]);
            const list = document.createElement(ordered ? 'ol' : 'ul');
            while (index < lines.length) {
                const item = lines[index].trim().match(/^([-+*]|\d+[.)])\s+(.+)$/);
                if (!item || /^\d/.test(item[1]) !== ordered) break;
                const listItem = document.createElement('li');
                appendInlineMarkdown(listItem, item[2]);
                list.appendChild(listItem);
                index++;
            }
            parent.appendChild(list);
            continue;
        }

        const paragraphLines = [trimmed];
        index++;
        while (index < lines.length && lines[index].trim() && !isMarkdownBlockStart(lines[index])) {
            paragraphLines.push(lines[index++].trim());
        }
        const paragraph = document.createElement('p');
        appendInlineMarkdown(paragraph, paragraphLines.join(' '));
        parent.appendChild(paragraph);
    }
}

function isMarkdownBlockStart(line) {
    const trimmed = line.trim();
    return /^(#{1,6}\s|```|~~~|>\s?|([-+*]|\d+[.)])\s+|---+\s*$|___+\s*$|\*\*\*+\s*$)/.test(trimmed);
}

function appendInlineMarkdown(parent, text) {
    const pattern = /(`+)([\s\S]+?)\1|\[([^\]]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)(?:\s+["'][^"']*["'])?\)|\*\*(.+?)\*\*|__(.+?)__|\*(\S(?:.*?\S)?)\*|_(\S(?:.*?\S)?)_/g;
    let lastIndex = 0;
    let match;
    while ((match = pattern.exec(text))) {
        parent.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
        if (match[1]) {
            const code = document.createElement('code');
            code.className = 'inline-code';
            code.textContent = match[2];
            parent.appendChild(code);
        } else if (match[3]) {
            const link = document.createElement('a');
            link.href = match[4];
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = match[3];
            parent.appendChild(link);
        } else if (match[5] || match[6]) {
            const strong = document.createElement('strong');
            strong.textContent = match[5] || match[6];
            parent.appendChild(strong);
        } else {
            const emphasis = document.createElement('em');
            emphasis.textContent = match[7] || match[8];
            parent.appendChild(emphasis);
        }
        lastIndex = pattern.lastIndex;
    }
    parent.appendChild(document.createTextNode(text.slice(lastIndex)));
}

function appendCodeBlock(parent, code, language) {
    const wrapper = document.createElement('div');
    wrapper.className = 'code-block';
    const toolbar = document.createElement('div');
    toolbar.className = 'code-toolbar';
    const languageLabel = document.createElement('span');
    languageLabel.className = 'code-language';
    languageLabel.textContent = language;
    const copyButton = document.createElement('button');
    copyButton.type = 'button';
    copyButton.className = 'copy-code-button';
    copyButton.setAttribute('aria-label', 'Copy code');
    copyButton.title = 'Copy code';
    copyButton.innerHTML = '<i class="fa-regular fa-copy" aria-hidden="true"></i><span>Copy</span>';
    copyButton.addEventListener('click', async () => {
        try {
            try {
                await navigator.clipboard.writeText(code);
            } catch (clipboardError) {
                const fallback = document.createElement('textarea');
                fallback.value = code;
                fallback.setAttribute('readonly', '');
                fallback.style.position = 'fixed';
                fallback.style.opacity = '0';
                document.body.appendChild(fallback);
                fallback.select();
                const copied = document.execCommand('copy');
                fallback.remove();
                if (!copied) throw clipboardError;
            }
            const label = copyButton.querySelector('span');
            label.textContent = 'Copied';
            copyButton.setAttribute('aria-label', 'Copied');
            copyButton.title = 'Copied';
            window.setTimeout(() => {
                label.textContent = 'Copy';
                copyButton.setAttribute('aria-label', 'Copy code');
                copyButton.title = 'Copy code';
            }, 1500);
        } catch (error) {
            copyButton.setAttribute('aria-label', 'Could not copy code');
            copyButton.title = 'Could not copy code';
        }
    });
    toolbar.append(languageLabel, copyButton);

    const pre = document.createElement('pre');
    const codeElement = document.createElement('code');
    codeElement.className = 'code-content';
    highlightCode(code, language, codeElement);
    pre.appendChild(codeElement);
    wrapper.append(toolbar, pre);
    parent.appendChild(wrapper);
}

function highlightCode(source, language, parent) {
    const commonKeywords = 'const let var function return if else for while class new import from export async await try catch throw true false null undefined';
    const pythonKeywords = 'def class return if elif else for while in not and or is None True False import from as async await try except raise with lambda yield pass break continue';
    const jsonKeywords = 'true false null';
    const keywords = language.toLowerCase().startsWith('py') ? pythonKeywords : language.toLowerCase() === 'json' ? jsonKeywords : commonKeywords;
    const tokenPattern = /(\/\/[^\n]*|#[^\n]*|\/\*[\s\S]*?\*\/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\b\d+(?:\.\d+)?\b|\b[A-Za-z_$][\w$]*\b|[{}()[\].,;:+*/%=<>!-])/g;
    let lastIndex = 0;
    let match;
    while ((match = tokenPattern.exec(source))) {
        parent.appendChild(document.createTextNode(source.slice(lastIndex, match.index)));
        const token = match[0];
        let type = '';
        if (/^(\/\/|#|\/\*)/.test(token)) type = 'syntax-comment';
        else if (/^["'`]/.test(token)) type = 'syntax-string';
        else if (/^\d/.test(token)) type = 'syntax-number';
        else if (keywords.split(' ').includes(token)) type = 'syntax-keyword';
        else if (/^[{}()[\].,;:+*/%=<>!-]$/.test(token)) type = 'syntax-punctuation';
        if (type) {
            const span = document.createElement('span');
            span.className = type;
            span.textContent = token;
            parent.appendChild(span);
        } else {
            parent.appendChild(document.createTextNode(token));
        }
        lastIndex = tokenPattern.lastIndex;
    }
    parent.appendChild(document.createTextNode(source.slice(lastIndex)));
}

function scrollToBottom() {
    chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
}
