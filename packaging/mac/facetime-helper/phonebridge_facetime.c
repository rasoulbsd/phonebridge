/*
 * Phonebridge FaceTime helper.
 *
 * Loaded into FaceTime on the Mac (next to BlueBubbles' own helper) so the Windows app can
 * ring someone straight from the Mac and then move the answered call to the PC:
 *
 *   1. POST /calls {"handle": "+15551234567", "video": true}  -> the Mac rings that person.
 *   2. When they answer, the Mac mutes itself, makes a FaceTime web link for the call and
 *      lets in the first person who opens it (the PC), then hangs up its own side.
 *   3. GET /calls/<id> reports each step and the link, so the PC knows when to open it.
 *
 * Only FaceTime may place calls without a confirmation prompt, which is why this runs inside
 * it. Every request must carry the BlueBubbles server password in X-Phonebridge-Token.
 *
 * Built in WSL without the macOS SDK (see build.sh), so every system declaration below is
 * written out by hand for the macOS x86_64 ABI. Nothing Objective-C runs during load: the
 * frameworks are not ready yet, so all work happens on FaceTime's main queue.
 */

typedef unsigned long size_t;
typedef long ssize_t;
typedef unsigned int socklen_t;
typedef unsigned char uint8_t;
typedef unsigned short uint16_t;
typedef unsigned int uint32_t;
typedef signed char BOOL;
typedef unsigned long NSUInteger;
typedef struct objc_object *id;
typedef struct objc_selector *SEL;
typedef struct _opaque_pthread_t *pthread_t;
typedef struct dispatch_queue_s *dispatch_queue_t;

#define NULL ((void *)0)
#define YES ((BOOL)1)
#define NO ((BOOL)0)

extern id objc_getClass(const char *);
extern SEL sel_registerName(const char *);
extern void objc_msgSend(void);
extern void *objc_autoreleasePoolPush(void);
extern void objc_autoreleasePoolPop(void *);
extern id objc_retain(id);
extern void objc_release(id);

extern struct dispatch_queue_s _dispatch_main_q;
extern void dispatch_sync_f(dispatch_queue_t, void *, void (*)(void *));
extern void dispatch_async_f(dispatch_queue_t, void *, void (*)(void *));

extern int socket(int, int, int);
extern int setsockopt(int, int, int, const void *, socklen_t);
extern int bind(int, const void *, socklen_t);
extern int listen(int, int);
extern int accept(int, void *, socklen_t *);
extern ssize_t read(int, void *, size_t);
extern ssize_t write(int, const void *, size_t);
extern int close(int);
extern int pthread_create(pthread_t *, const void *, void *(*)(void *), void *);
extern int usleep(unsigned int);
extern char *getenv(const char *);
extern int unsetenv(const char *);
extern size_t strlen(const char *);
extern int strcmp(const char *, const char *);
extern int strncmp(const char *, const char *, size_t);
extern int strncasecmp(const char *, const char *, size_t);
extern char *strstr(const char *, const char *);
extern char *strdup(const char *);
extern void *memcpy(void *, const void *, size_t);
extern void *memset(void *, int, size_t);
extern void *malloc(size_t);
extern void free(void *);
extern long strtol(const char *, char **, int);
extern int snprintf(char *, size_t, const char *, ...);
extern double CFAbsoluteTimeGetCurrent(void);
extern void NSLog(id, ...);

/* BSD socket constants and layouts on macOS. */
#define AF_INET 2
#define SOCK_STREAM 1
#define SOL_SOCKET 0xffff
#define SO_REUSEADDR 0x0004
#define SO_SNDTIMEO 0x1005
#define SO_RCVTIMEO 0x1006
#define SO_NOSIGPIPE 0x1022

struct mac_sockaddr_in {
    uint8_t sin_len;
    uint8_t sin_family;
    uint16_t sin_port;
    uint32_t sin_addr;
    char sin_zero[8];
};

struct mac_timeval {
    long tv_sec;
    int tv_usec;
};

/* TUCallStatus values. */
#define CALL_ACTIVE 1
#define CALL_SENDING 3
#define CALL_DISCONNECTED 6

#define DEFAULT_PORT 1235
#define MAX_SESSIONS 8
#define HEADER_LIMIT 8192
#define BODY_LIMIT 4096
/* How long the PC has to open the link once the person answers. */
#define JOIN_TIMEOUT 120.0
/* The Mac hangs up once the PC is in, or after this long regardless. */
#define LEAVE_AFTER_ADMIT 25.0
#define LEAVE_GRACE 3.0
/* Without a conversation to link, the call cannot move to the PC. */
#define CONVERSATION_TIMEOUT 15.0
#define IDLE_TIMEOUT 10.0
#define UNREACHABLE_WITHIN 5.0
/* Finished calls stay queryable for a while, then their slot is reused. */
#define KEEP_FINISHED 600.0

typedef enum {
    PHASE_FREE = 0,
    PHASE_CALLING,
    PHASE_ANSWERED,
    PHASE_WAITING_FOR_PC,
    PHASE_JOINING,
    PHASE_ON_PC,
    PHASE_ENDED,
    PHASE_FAILED,
} phase_t;

static const char *const PHASE_NAMES[] = {
    "free", "calling", "answered", "waiting_for_pc", "joining", "on_pc", "ended", "failed",
};

typedef struct {
    phase_t phase;
    char id[40];
    char call_uuid[40];
    id call;          /* retained TUCall */
    BOOL saw_live;
    BOOL started;
    BOOL muted;
    BOOL link_requested;
    char *link;       /* malloc'd FaceTime web link */
    double created;
    double answered_at;
    double link_at;
    double admitted_at;
    double finished_at;
    char reason[160];
} session_t;

static session_t g_sessions[MAX_SESSIONS];
static char *g_token;
static int g_port = DEFAULT_PORT;
static volatile int g_enabled = -1; /* -1 unknown, 0 not FaceTime, 1 running */

/* ------------------------------------------------------------- Objective-C helpers */

static SEL S(const char *name) { return sel_registerName(name); }
static id C(const char *name) { return objc_getClass(name); }

static id msg(id obj, const char *sel) { return ((id(*)(id, SEL))objc_msgSend)(obj, S(sel)); }
static id msg1(id obj, const char *sel, id a) { return ((id(*)(id, SEL, id))objc_msgSend)(obj, S(sel), a); }
static id msg2(id obj, const char *sel, id a, id b) {
    return ((id(*)(id, SEL, id, id))objc_msgSend)(obj, S(sel), a, b);
}
static void msg_set_bool(id obj, const char *sel, BOOL v) {
    ((void (*)(id, SEL, BOOL))objc_msgSend)(obj, S(sel), v);
}
static BOOL msg_bool(id obj, const char *sel) { return ((BOOL(*)(id, SEL))objc_msgSend)(obj, S(sel)); }
static int msg_int(id obj, const char *sel) { return ((int (*)(id, SEL))objc_msgSend)(obj, S(sel)); }
static NSUInteger msg_count(id obj) { return ((NSUInteger(*)(id, SEL))objc_msgSend)(obj, S("count")); }
static id msg_at(id array, NSUInteger i) {
    return ((id(*)(id, SEL, NSUInteger))objc_msgSend)(array, S("objectAtIndex:"), i);
}
static BOOL is_kind(id obj, const char *cls) {
    return obj && ((BOOL(*)(id, SEL, id))objc_msgSend)(obj, S("isKindOfClass:"), C(cls));
}

static id ns(const char *s) {
    return ((id(*)(id, SEL, const char *))objc_msgSend)(C("NSString"), S("stringWithUTF8String:"), s);
}
static const char *utf8(id s) { return s ? ((const char *(*)(id, SEL))objc_msgSend)(s, S("UTF8String")) : NULL; }

static void log_line(const char *text) { NSLog(ns("Phonebridge: %s"), text); }

static void copy_str(char *dst, size_t size, const char *src) {
    size_t n = 0;
    if (src) {
        while (src[n] && n + 1 < size) {
            dst[n] = src[n];
            n++;
        }
    }
    dst[n] = 0;
}

/* ------------------------------------------------------------- sessions */

static session_t *find_session(const char *sid) {
    for (int i = 0; i < MAX_SESSIONS; i++) {
        if (g_sessions[i].phase != PHASE_FREE && strcmp(g_sessions[i].id, sid) == 0) return &g_sessions[i];
    }
    return NULL;
}

static BOOL is_finished(phase_t phase) {
    return phase == PHASE_ON_PC || phase == PHASE_ENDED || phase == PHASE_FAILED;
}

static void release_session(session_t *s) {
    if (s->call) objc_release(s->call);
    free(s->link);
    memset(s, 0, sizeof *s);
}

static session_t *new_session(void) {
    double now = CFAbsoluteTimeGetCurrent();
    for (int i = 0; i < MAX_SESSIONS; i++) {
        session_t *s = &g_sessions[i];
        if (s->phase != PHASE_FREE && is_finished(s->phase) && now - s->finished_at > KEEP_FINISHED) {
            release_session(s);
        }
    }
    for (int i = 0; i < MAX_SESSIONS; i++) {
        if (g_sessions[i].phase == PHASE_FREE) return &g_sessions[i];
    }
    return NULL;
}

static void finish(session_t *s, phase_t phase, const char *reason) {
    s->phase = phase;
    s->finished_at = CFAbsoluteTimeGetCurrent();
    if (reason) copy_str(s->reason, sizeof s->reason, reason);
    if (s->call) {
        objc_release(s->call);
        s->call = NULL;
    }
}

static id call_center(void) { return msg(C("TUCallCenter"), "sharedInstance"); }

/* The TUCall for a session, refreshed from the call center once its UUID is known. */
static id live_call(session_t *s) {
    if (!s->call) return NULL;
    if (!s->call_uuid[0]) {
        const char *uuid = utf8(msg(s->call, "callUUID"));
        if (uuid) copy_str(s->call_uuid, sizeof s->call_uuid, uuid);
    }
    if (!s->call_uuid[0]) return s->call;
    id live = msg1(call_center(), "callWithCallUUID:", ns(s->call_uuid));
    if (live) {
        s->saw_live = YES;
        return live;
    }
    return s->saw_live ? NULL : s->call;
}

static void hang_up(id call) {
    if (call) ((void (*)(id, SEL, id))objc_msgSend)(call_center(), S("disconnectCall:"), call);
}

typedef struct {
    char id[40];
    char *url;
} link_result_t;

static void store_link(void *ctx) {
    link_result_t *result = ctx;
    session_t *s = find_session(result->id);
    if (s && !is_finished(s->phase) && !s->link) {
        if (result->url) {
            s->link = result->url;
            result->url = NULL;
            s->link_at = CFAbsoluteTimeGetCurrent();
            s->phase = PHASE_WAITING_FOR_PC;
            log_line("call link ready");
        } else {
            hang_up(live_call(s));
            finish(s, PHASE_FAILED, "FaceTime couldn't make a link to move the call to this PC.");
        }
    }
    free(result->url);
    free(result);
}

static void request_link(session_t *s, id conversation) {
    s->link_requested = YES;
    link_result_t *result = malloc(sizeof *result);
    copy_str(result->id, sizeof result->id, s->id);
    result->url = NULL;
    id client = msg(msg(C("TUConversationManagerXPCClient"), "alloc"), "init");
    void (^handler)(id, id) = ^(id link, id error) {
        (void)error;
        void *pool = objc_autoreleasePoolPush();
        const char *url = utf8(msg(msg(link, "URL"), "absoluteString"));
        result->url = url ? strdup(url) : NULL;
        objc_autoreleasePoolPop(pool);
        dispatch_async_f(&_dispatch_main_q, result, store_link);
    };
    ((void (*)(id, SEL, id, void (^)(id, id)))objc_msgSend)(
        client, S("generateLinkForConversation:completionHandler:"), conversation, handler);
}

/* Lets in the first person waiting to join: the PC that just opened the link. */
static BOOL admit_first_pending(id conversation) {
    id pending = msg(msg(conversation, "pendingMembers"), "allObjects");
    if (msg_count(pending) == 0) return NO;
    id manager = msg(msg(C("TUConversationManager"), "alloc"), "init");
    msg2(manager, "approvePendingMember:forConversation:", msg_at(pending, 0), conversation);
    return YES;
}

static void advance(session_t *s, double now) {
    id call = live_call(s);
    int status = call ? msg_int(call, "callStatus") : CALL_DISCONNECTED;
    if (status == CALL_SENDING || status == CALL_ACTIVE) s->started = YES;
    /* A just-dialled call can report idle for a moment before it starts sending. */
    BOOL idle_too_long = status == 0 && (s->started || now - s->created > IDLE_TIMEOUT);

    if (status == CALL_DISCONNECTED || idle_too_long) {
        if (s->phase == PHASE_CALLING) {
            const char *why = call ? utf8(msg(call, "endedReasonString")) : NULL;
            /* A call that ends within seconds never rang: the address isn't on FaceTime. */
            const char *fallback = now - s->created < UNREACHABLE_WITHIN
                                       ? "FaceTime couldn't reach them. They may not use FaceTime."
                                       : "No answer.";
            finish(s, PHASE_ENDED, why && *why ? why : fallback);
        } else {
            finish(s, PHASE_ENDED, "The call ended.");
        }
        return;
    }

    if (status != CALL_ACTIVE) return;

    if (!s->muted) {
        /* Nobody is at the Mac: keep its camera and microphone out of the call. FaceTime
         * refuses this while the call is still ringing, so it waits for the answer. */
        msg_set_bool(call, "setUplinkMuted:", YES);
        msg_set_bool(call, "setIsSendingVideo:", NO);
        s->muted = YES;
    }

    if (s->phase == PHASE_CALLING) {
        s->phase = PHASE_ANSWERED;
        s->answered_at = now;
        log_line("call answered");
    }

    id conversation = msg1(call_center(), "activeConversationForCall:", call);
    if (!conversation) {
        if (now - s->answered_at > CONVERSATION_TIMEOUT) {
            hang_up(call);
            finish(s, PHASE_FAILED, "This call can't be moved to the PC.");
        }
        return;
    }

    if (!s->link_requested) {
        request_link(s, conversation);
        return;
    }

    if (s->phase == PHASE_WAITING_FOR_PC) {
        if (admit_first_pending(conversation)) {
            s->phase = PHASE_JOINING;
            s->admitted_at = now;
            log_line("let the PC in");
        } else if (now - s->link_at > JOIN_TIMEOUT) {
            hang_up(call);
            finish(s, PHASE_FAILED, "The PC didn't join in time, so the call was ended.");
        }
        return;
    }

    if (s->phase == PHASE_JOINING) {
        NSUInteger others = msg_count(msg(conversation, "activeRemoteParticipants"));
        double since = now - s->admitted_at;
        if ((others >= 2 && since >= LEAVE_GRACE) || since >= LEAVE_AFTER_ADMIT) {
            hang_up(call);
            finish(s, PHASE_ON_PC, NULL);
            log_line("call moved to the PC");
        }
    }
}

static void tick(void *ctx) {
    (void)ctx;
    if (g_enabled != 1) return;
    void *pool = objc_autoreleasePoolPush();
    double now = CFAbsoluteTimeGetCurrent();
    for (int i = 0; i < MAX_SESSIONS; i++) {
        session_t *s = &g_sessions[i];
        if (s->phase != PHASE_FREE && !is_finished(s->phase)) advance(s, now);
    }
    objc_autoreleasePoolPop(pool);
}

/* ------------------------------------------------------------- HTTP */

typedef struct {
    char method[8];
    char path[128];
    char body[BODY_LIMIT + 1];
    size_t body_len;
    int status;
    char *response;
    size_t response_len;
} request_t;

static void respond(request_t *r, int status, id payload) {
    id data = ((id(*)(id, SEL, id, NSUInteger, id *))objc_msgSend)(
        C("NSJSONSerialization"), S("dataWithJSONObject:options:error:"), payload, 0, NULL);
    NSUInteger len = data ? ((NSUInteger(*)(id, SEL))objc_msgSend)(data, S("length")) : 0;
    r->status = status;
    r->response = malloc(len + 1);
    if (len) memcpy(r->response, ((const void *(*)(id, SEL))objc_msgSend)(data, S("bytes")), len);
    r->response_len = len;
}

static id dict(void) { return msg(C("NSMutableDictionary"), "dictionary"); }
static void put(id d, const char *key, id value) {
    if (value) msg2(d, "setObject:forKey:", value, ns(key));
}
static id boolean(BOOL v) {
    return ((id(*)(id, SEL, BOOL))objc_msgSend)(C("NSNumber"), S("numberWithBool:"), v);
}

static void respond_error(request_t *r, int status, const char *text) {
    id d = dict();
    put(d, "error", ns(text));
    respond(r, status, d);
}

static id session_json(session_t *s) {
    id d = dict();
    put(d, "id", ns(s->id));
    put(d, "phase", ns(PHASE_NAMES[s->phase]));
    if (s->link) put(d, "link", ns(s->link));
    if (s->reason[0]) put(d, "reason", ns(s->reason));
    return d;
}

static id body_json(request_t *r) {
    if (r->body_len == 0) return NULL;
    id data = ((id(*)(id, SEL, const void *, NSUInteger))objc_msgSend)(
        C("NSData"), S("dataWithBytes:length:"), r->body, r->body_len);
    id parsed = ((id(*)(id, SEL, id, NSUInteger, id *))objc_msgSend)(
        C("NSJSONSerialization"), S("JSONObjectWithData:options:error:"), data, 0, NULL);
    return is_kind(parsed, "NSDictionary") ? parsed : NULL;
}

static void start_call(request_t *r) {
    id body = body_json(r);
    id handle_text = body ? msg1(body, "objectForKey:", ns("handle")) : NULL;
    if (!is_kind(handle_text, "NSString") || msg_int(handle_text, "length") == 0 ||
        msg_int(handle_text, "length") > 128) {
        respond_error(r, 400, "Say who to call.");
        return;
    }
    id video_value = msg1(body, "objectForKey:", ns("video"));
    BOOL video = is_kind(video_value, "NSNumber") ? msg_bool(video_value, "boolValue") : YES;

    id center = call_center();
    if (msg_count(msg(center, "currentCalls")) > 0) {
        respond_error(r, 409, "The Mac is already on a call.");
        return;
    }
    id provider = msg(msg(center, "providerManager"), "faceTimeProvider");
    if (!provider) {
        respond_error(r, 503, "FaceTime isn't ready on the Mac.");
        return;
    }
    id handle = msg1(C("TUHandle"), "normalizedHandleWithDestinationID:", handle_text);
    if (!handle) handle = msg1(C("TUHandle"), "handleWithDestinationID:", handle_text);
    id request = msg1(msg(C("TUDialRequest"), "alloc"), "initWithProvider:", provider);
    msg1(request, "setHandle:", handle);
    msg_set_bool(request, "setVideo:", video);
    msg_set_bool(request, "setShowUIPrompt:", NO);
    if (!handle || !msg_bool(request, "isValid")) {
        objc_release(request);
        respond_error(r, 400, "FaceTime can't call that address.");
        return;
    }
    session_t *s = new_session();
    if (!s) {
        objc_release(request);
        respond_error(r, 503, "Too many calls in progress.");
        return;
    }
    id call = msg1(center, "dialWithRequest:", request);
    objc_release(request);
    if (!call) {
        respond_error(r, 502, "FaceTime didn't start the call.");
        return;
    }
    memset(s, 0, sizeof *s);
    copy_str(s->id, sizeof s->id, utf8(msg(msg(C("NSUUID"), "UUID"), "UUIDString")));
    s->call = objc_retain(call);
    s->created = CFAbsoluteTimeGetCurrent();
    s->phase = PHASE_CALLING;
    log_line("calling");
    respond(r, 201, session_json(s));
}

static void end_call(request_t *r, session_t *s) {
    if (!is_finished(s->phase)) {
        hang_up(live_call(s));
        finish(s, PHASE_ENDED, "You ended the call.");
    }
    respond(r, 200, session_json(s));
}

static void route(void *ctx) {
    request_t *r = ctx;
    void *pool = objc_autoreleasePoolPush();
    BOOL get = strcmp(r->method, "GET") == 0;
    BOOL post = strcmp(r->method, "POST") == 0;
    const char *calls = "/calls/";
    size_t calls_len = strlen(calls);

    if (get && strcmp(r->path, "/health") == 0) {
        id d = dict();
        put(d, "ok", boolean(YES));
        put(d, "version", ns("1"));
        respond(r, 200, d);
    } else if (post && strcmp(r->path, "/calls") == 0) {
        start_call(r);
    } else if (strncmp(r->path, calls, calls_len) == 0) {
        char sid[40];
        const char *rest = r->path + calls_len;
        const char *slash = strstr(rest, "/");
        size_t n = slash ? (size_t)(slash - rest) : strlen(rest);
        if (n >= sizeof sid) n = sizeof sid - 1;
        memcpy(sid, rest, n);
        sid[n] = 0;
        session_t *s = find_session(sid);
        if (!s) {
            respond_error(r, 404, "No such call.");
        } else if (get && !slash) {
            respond(r, 200, session_json(s));
        } else if (post && slash && strcmp(slash, "/end") == 0) {
            end_call(r, s);
        } else {
            respond_error(r, 404, "Not found.");
        }
    } else {
        respond_error(r, 404, "Not found.");
    }
    objc_autoreleasePoolPop(pool);
}

static BOOL token_matches(const char *given) {
    if (!g_token || !*g_token || !given) return NO;
    size_t a = strlen(g_token), b = strlen(given);
    unsigned diff = (unsigned)(a ^ b);
    for (size_t i = 0; i < a; i++) diff |= (unsigned)(g_token[i] ^ given[i < b ? i : 0]);
    return diff == 0;
}

/* Copies a header's value, or returns NO when the request does not have it. */
static BOOL header(const char *headers, const char *name, char *out, size_t size) {
    size_t name_len = strlen(name);
    const char *line = strstr(headers, "\r\n");
    while (line && line[2] && !(line[2] == '\r' && line[3] == '\n')) {
        line += 2;
        if (strncasecmp(line, name, name_len) == 0 && line[name_len] == ':') {
            const char *v = line + name_len + 1;
            while (*v == ' ' || *v == '\t') v++;
            size_t n = 0;
            while (v[n] && v[n] != '\r' && n + 1 < size) {
                out[n] = v[n];
                n++;
            }
            out[n] = 0;
            return YES;
        }
        line = strstr(line, "\r\n");
    }
    return NO;
}

static void send_all(int fd, const char *data, size_t len) {
    while (len > 0) {
        ssize_t n = write(fd, data, len);
        if (n <= 0) return;
        data += n;
        len -= (size_t)n;
    }
}

static const char *reason_phrase(int status) {
    switch (status) {
        case 200: return "OK";
        case 201: return "Created";
        case 400: return "Bad Request";
        case 401: return "Unauthorized";
        case 404: return "Not Found";
        case 409: return "Conflict";
        case 413: return "Payload Too Large";
        case 502: return "Bad Gateway";
        default: return "Service Unavailable";
    }
}

static void reply_raw(int fd, int status, const char *json, size_t len) {
    char head[256];
    int n = snprintf(head, sizeof head,
                     "HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nContent-Length: %lu\r\n"
                     "Cache-Control: no-store\r\nConnection: close\r\n\r\n",
                     status, reason_phrase(status), (unsigned long)len);
    send_all(fd, head, (size_t)n);
    send_all(fd, json, len);
}

static void serve_connection(int fd) {
    int one = 1;
    struct mac_timeval timeout = {5, 0};
    setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout);
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof timeout);

    char buf[HEADER_LIMIT + BODY_LIMIT + 1];
    size_t used = 0;
    char *end = NULL;
    while (used < HEADER_LIMIT) {
        ssize_t n = read(fd, buf + used, HEADER_LIMIT - used);
        if (n <= 0) return;
        used += (size_t)n;
        buf[used] = 0;
        if ((end = strstr(buf, "\r\n\r\n"))) break;
    }
    if (!end) return;

    request_t *r = malloc(sizeof *r);
    memset(r, 0, sizeof *r);
    const char *sp1 = strstr(buf, " ");
    const char *sp2 = sp1 ? strstr(sp1 + 1, " ") : NULL;
    if (!sp1 || !sp2 || (size_t)(sp1 - buf) >= sizeof r->method || (size_t)(sp2 - sp1 - 1) >= sizeof r->path) {
        free(r);
        return;
    }
    memcpy(r->method, buf, (size_t)(sp1 - buf));
    memcpy(r->path, sp1 + 1, (size_t)(sp2 - sp1 - 1));

    char value[256];
    const char *unauthorized = "{\"error\":\"Wrong password.\"}";
    if (!header(buf, "X-Phonebridge-Token", value, sizeof value) || !token_matches(value)) {
        reply_raw(fd, 401, unauthorized, strlen(unauthorized));
        free(r);
        return;
    }
    memset(value, 0, sizeof value);

    size_t want = 0;
    if (header(buf, "Content-Length", value, sizeof value)) want = (size_t)strtol(value, NULL, 10);
    if (want > BODY_LIMIT) {
        const char *big = "{\"error\":\"Request too large.\"}";
        reply_raw(fd, 413, big, strlen(big));
        free(r);
        return;
    }
    char *body = end + 4;
    size_t have = used - (size_t)(body - buf);
    while (have < want) {
        ssize_t n = read(fd, body + have, want - have);
        if (n <= 0) break;
        have += (size_t)n;
    }
    if (have > want) have = want;
    memcpy(r->body, body, have);
    r->body_len = have;

    dispatch_sync_f(&_dispatch_main_q, r, route);
    reply_raw(fd, r->status, r->response ? r->response : "{}", r->response ? r->response_len : 2);
    free(r->response);
    free(r);
}

static void check_process(void *ctx) {
    (void)ctx;
    void *pool = objc_autoreleasePoolPush();
    const char *bundle = utf8(msg(msg(C("NSBundle"), "mainBundle"), "bundleIdentifier"));
    g_enabled = bundle && strcmp(bundle, "com.apple.FaceTime") == 0;
    objc_autoreleasePoolPop(pool);
}

static void hide_app(void *ctx) {
    (void)ctx;
    void *pool = objc_autoreleasePoolPush();
    msg1(msg(C("NSApplication"), "sharedApplication"), "hide:", NULL);
    objc_autoreleasePoolPop(pool);
}

static void *server_thread(void *arg) {
    (void)arg;
    dispatch_sync_f(&_dispatch_main_q, NULL, check_process);
    if (g_enabled != 1) return NULL;
    if (!g_token) {
        dispatch_sync_f(&_dispatch_main_q, "no password set; not listening", (void (*)(void *))log_line);
        return NULL;
    }
    int fd = -1;
    for (;;) {
        fd = socket(AF_INET, SOCK_STREAM, 0);
        int one = 1;
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof one);
        struct mac_sockaddr_in addr;
        memset(&addr, 0, sizeof addr);
        addr.sin_len = sizeof addr;
        addr.sin_family = AF_INET;
        addr.sin_port = (uint16_t)(((g_port & 0xff) << 8) | ((g_port >> 8) & 0xff));
        if (fd >= 0 && bind(fd, &addr, sizeof addr) == 0 && listen(fd, 8) == 0) break;
        if (fd >= 0) close(fd);
        usleep(3000000);
    }
    dispatch_async_f(&_dispatch_main_q, "listening for calls", (void (*)(void *))log_line);
    for (;;) {
        int client = accept(fd, NULL, NULL);
        if (client < 0) {
            usleep(100000);
            continue;
        }
        serve_connection(client);
        close(client);
    }
    return NULL;
}

static void *ticker_thread(void *arg) {
    (void)arg;
    usleep(4000000);
    dispatch_async_f(&_dispatch_main_q, NULL, hide_app);
    for (;;) {
        if (g_enabled == 0) return NULL;
        dispatch_async_f(&_dispatch_main_q, NULL, tick);
        usleep(500000);
    }
    return NULL;
}

__attribute__((constructor)) static void phonebridge_load(void) {
    const char *token = getenv("PHONEBRIDGE_TOKEN");
    if (token && *token) g_token = strdup(token);
    unsetenv("PHONEBRIDGE_TOKEN");
    const char *port = getenv("PHONEBRIDGE_PORT");
    if (port && *port) {
        long p = strtol(port, NULL, 10);
        if (p > 0 && p < 65536) g_port = (int)p;
    }
    pthread_t thread;
    pthread_create(&thread, NULL, server_thread, NULL);
    pthread_create(&thread, NULL, ticker_thread, NULL);
}
