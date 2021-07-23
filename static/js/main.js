jQuery(function ($) {
  var minionLoginFormID = '#minion-login-form',
    minionLoginBtn = $('#minion-login-btn'),
    websshLoginFormID = '#webssh-login-form',
    websshLoginBtn = $('#webssh-login-btn'),
    info = $('#info'),
    toolbar = $('#toolbar'),
    menu = $('#menu'),
    progress = $("#progress"),
    clean = $("#clean"),
    cell = {},
    titleElement = document.querySelector('title'),
    customizedFont = "Hack",  // Named by style.css
    fields = ["hostname", "port", "username", "password"],
    defaultTitle = "Term1nal",
    currentTitle = undefined,
    reader = {},
    term = new Terminal();


  // Hide toolbar first
  toolbar.hide();
  menu.hide();
  // popupForm.hide();

  function setMsg(text) {
    $('#msg').html(text);
  }

  function base64ArrayBuffer(arrayBuffer) {
    var base64 = ''
    var encodings = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'

    var bytes = new Uint8Array(arrayBuffer)
    var byteLength = bytes.byteLength
    var byteRemainder = byteLength % 3
    var mainLength = byteLength - byteRemainder

    var a, b, c, d
    var chunk

    // Main loop deals with bytes in chunks of 3
    for (var i = 0; i < mainLength; i = i + 3) {
      // Combine the three bytes into a single integer
      chunk = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2]

      // Use bitmasks to extract 6-bit segments from the triplet
      a = (chunk & 16515072) >> 18 // 16515072 = (2^6 - 1) << 18
      b = (chunk & 258048) >> 12 // 258048   = (2^6 - 1) << 12
      c = (chunk & 4032) >> 6 // 4032     = (2^6 - 1) << 6
      d = chunk & 63               // 63       = 2^6 - 1

      // Convert the raw binary segments to the appropriate ASCII encoding
      base64 += encodings[a] + encodings[b] + encodings[c] + encodings[d]
    }

    // Deal with the remaining bytes and padding
    if (byteRemainder == 1) {
      chunk = bytes[mainLength]

      a = (chunk & 252) >> 2 // 252 = (2^6 - 1) << 2

      // Set the 4 least significant bits to zero
      b = (chunk & 3) << 4 // 3   = 2^2 - 1

      base64 += encodings[a] + encodings[b] + '=='
    } else if (byteRemainder == 2) {
      chunk = (bytes[mainLength] << 8) | bytes[mainLength + 1]

      a = (chunk & 64512) >> 10 // 64512 = (2^6 - 1) << 10
      b = (chunk & 1008) >> 4 // 1008  = (2^6 - 1) << 4

      // Set the 2 least significant bits to zero
      c = (chunk & 15) << 2 // 15    = 2^4 - 1

      base64 += encodings[a] + encodings[b] + encodings[c] + '='
    }

    return base64
  }

  function copySelectedText() {
    let el = document.createElement('textarea');
    el.value = term.getSelection();
    el.select();
    document.execCommand('copy');
  }

  function fillClientsTable(clients) {
    $("#onlineCli").text(clients.length);

    tbody = "";
    clients.forEach(function (client) {
      tbody += `
      <tr>
        <td>${client.name}</td>
        <td>${client.ip}</td>
        <td>${client.publicip}</td>
        <td>${client.port}</td>
        <td id="connect">
          <a class="link" data-value="${client.port}" href="javascript: void(0)">Connect</a>
        </td>
      </tr>
      `
    })
    $("#clientsTbody").html(tbody);
  }

  // Maybe cancel this after using direct upload/download
  function setSession(name, data) {
    window.sessionStorage.clear()
    window.sessionStorage.setItem(name, data)
  }

  function getSession(name) {
    return window.sessionStorage.getItem(name)
  }

  function validateFormData(formID, formFields) {
    let data = new FormData(document.querySelector(formID));
    let result = { error: "" }

    formFields.forEach(function (attr) {
      var val = data.get(attr)
      if (!val) {
        result.error = `${attr} is required`;
        return result;
      }
      // } else {
      //   result[attr] = val;
      // }
    })

    // Set current tab title
    currentTitle = `${data.get('username')}@port:${data.get('port')}`;
    return result;
  }

  function getCurrentDimension(term) {
    if (!cell.width || !cell.height) {
      try {
        cell.width = term._core._renderService._renderer.dimensions.actualCellWidth;
        cell.height = term._core._renderService._renderer.dimensions.actualCellHeight;
      } catch (error) {
        console.log("Error getting curent Dimension")
      }
    }

    let cols = parseInt(window.innerWidth / cell.width, 10),
      rows = parseInt(window.innerHeight / cell.height, 10);
    return [cols, rows];
  }

  function resizeTerminal(term) {
    let dim = getCurrentDimension(term);
    term.resizeWindow(dim[0], dim[1]);
  }

  // Use window.Textdecoder to process terminal data from server,
  // then write to Xterm.js
  function processBlobData(blob, callback, decoder) {
    if (window.TextDecoder) {
      let reader = new window.FileReader();

      reader.onload = function () {
        let text;
        try {
          text = decoder.decode(reader.result);
        } catch (err) {
          console.log(`!!! Decode error: ${err}`);
        } finally {
          callback(text);
        }

      }
      reader.onerror = function (err) {
        console.log(`Filereader onerror: ${err}`)
      }
      reader.readAsArrayBuffer(blob);
    } else {
      console.log("!!! Browser does not support TextDecoder");
    }
  }


  function connectCallback(resp) {
    // Enable login button
    minionLoginBtn.attr('disabled', false);

    if (resp.status !== 200) {
      setMsg(`${resp.status}: ${resp.statusText}`);
      return;
    }

    let defaultEncoding = 'utf-8',
      msg = resp.responseJSON;

    if (!msg.id) {
      err = msg.status.toLowerCase();
      if (err.startsWith('unable to connect to localhost')) {
        // Encounter dangling Minion, reload clients
        loadClients();
      }
      setMsg(msg.status);
      return;
    } else {
      setSession("minion", msg.id)
    }

    if (!msg.encoding) {
      // Use default encoding when unable to detect serer encoding
      // msg.encoding = defaultEncoding;
      console.log(`Use default encoding: ${defaultEncoding}`);
      var decoder = defaultEncoding;
    } else {
      console.log(`Server encoding : ${msg.encoding}`);
      try {
        var decoder = new window.TextDecoder(msg.encoding);
      } catch (EncodingError) {
        console.log(`Unknown encoding: ${msg.encoding}`);
      }
    }

    // Prepare websocket
    let proto = window.location.protocol,
      url = window.location.href,
      char = (proto === "http:" ? "ws:" : "wss:"),
      wsURL = `${url.replace(proto, char)}ws?id=${msg.id}`,
      sock = new window.WebSocket(wsURL),
      terminal = document.getElementById("terminal"),
      term = new window.Terminal({
        cursorBlink: true,
        theme: {
          background: "black"
        }
      });
    term.fitAddon = new window.FitAddon.FitAddon();
    term.loadAddon(term.fitAddon);


    function write2terminal(text) {
      if (term) {
        term.write(text);
        if (!term.resized) {
          resizeTerminal(term);
          term.resized = true;
        }
      }
    }

    term.resizeWindow = function (cols, rows) {
      if (cols !== this.cols || rows !== this.rows) {
        console.log('Resizing terminal to geometry: ' + JSON.stringify({ 'cols': cols, 'rows': rows }));
        this.resize(cols, rows);
        sock.send(JSON.stringify({ 'resize': [cols, rows] }));
      }
    };

    term.onData(function (data) {
      sock.send(JSON.stringify({ 'data': data }));
    });

    // Copy on selection
    window.addEventListener('mouseup', copySelectedText);

    sock.onopen = function () {
      menu.show();

      term.open(terminal);

      //Full screen
      $('#terminal .terminal').toggleClass('fullscreen');
      term.fitAddon.fit();

      term.setOption('fontFamily', customizedFont);
      term.focus();
      // titleElement.text = tmpData.title || defaultTitle;
      titleElement.text = currentTitle || defaultTitle;
    };

    sock.onmessage = function (msg) {
      processBlobData(msg.data, write2terminal, decoder);
    };

    sock.onerror = function (event) {
      console.error(event);
    };

    sock.onclose = function (event) {
      // Hide toolbar again
      toolbar.hide();
      menu.hide();

      sock = undefined;
      term.dispose();
      term = undefined;
      setMsg(event.reason);
      titleElement.text = defaultTitle;

      // Remove some event listeners
      window.removeEventListener("mouseup", copySelectedText);
    };

    $(window).resize(function () {
      if (term) {
        resizeTerminal(term);
      }
    });
  } // ajaxCallback()

  function connect(formID) {
    // Disable login button
    minionLoginBtn.attr('disabled', false);

    let data = new FormData(document.querySelector(formID))
    console.log(JSON.stringify(Object.fromEntries(data)));

    $.ajax({
      url: '/',
      type: 'post',
      // data: JSON.stringify({"port": port}),
      data: JSON.stringify(Object.fromEntries(data)),
      complete: connectCallback,
      error: function () {
        console.log("wtf");
      },
      cache: false,
      contentType: false,
      processData: false
    });

    document.getElementById('login-dialog').close();
  } // connect function

  function loadClients() {
    $.ajax({
      url: '/clients',
      type: 'GET',

    }).done(function (resp) {
      clients = JSON.parse(resp);
      fillClientsTable(clients);

    }).fail(function (resp) {
      console.log('Load clients failed');
      console.log(resp.status);
      console.log(resp);
    });
  }

  // Detect Gru mode by getting element
  if (document.getElementById('onlineCli')) {
    loadClients();
  }

  // ====================================
  // Setup Event Listeners
  // ====================================
  minionLoginBtn.click(function (event) {
    event.preventDefault();
    // Clean msg
    setMsg("");
    let result = validateFormData(minionLoginFormID, ["username", "password"]);
    if (result.error) {
      setMsg(result.error);

    } else {
      connect(minionLoginFormID);
      console.log(`minion login result: ${result}`);
    }
  });
  websshLoginBtn.click(function (event) {
    event.preventDefault();
    // Clean msg
    setMsg("");
    let result = validateFormData(websshLoginFormID, fields);
    if (result.error) {
      setMsg(result.error);
    } else {
      connect(websshLoginFormID);
      console.log(result);
    }
  });

  $("#upload").click(function () {
    // Clean this for triggering change event for same file
    this.value = "";
    // Clean info text
    info.text("");
  });

  // Listen to "file" change event to upload file,
  // monitor "progress" event to calculate uploading percentage
  $("#upload").change(function () {
    const file = this.files[0];
    const filename = this.files[0].name;
    path = `/upload?minion=${getSession("minion")}&file=${filename}`

    //changed to sandbox, becuase we cannot have nice things
    const url = window.location.origin + path;
    // const url = "http://10.0.0.229:8000" + path;


    var reader = {};
    const chunkSize = 1024 * 1024 * 2;
    const totalSize = file.size;

    reader = new FileReader();
    upload_file(0)
    progress.show();


    function upload_file(start) {
      var next_slice = start + chunkSize + 1;
      console.log(`chunk: ${start} - ${next_slice}, total: ${totalSize}`);
      var blob = file.slice(start, next_slice);

      reader.onloadend = function (event) {
        if (event.target.readyState !== FileReader.DONE) {
          return;
        }

        // console.log(event)
        // var raw = event.target.result.split(',')[1];
        // console.log("Raw data:");
        // console.log(event.target.result);
        // console.log(base64ArrayBuffer(event.target.result))
        $.ajax({
          url: url,
          type: 'POST',
          contentType: false,
          processData: false,
          cache: false,
          // data: base64ArrayBuffer(event.target.result),
          data: event.target.result.split(',')[1],
          error: function (jqXHR, textStatus, errorThrown) {
            console.log("errorrrrrrrrrrrrrrrrrrr");
            console.log(jqXHR);
            console.log(textStatus);
            console.log(errorThrown);
            console.log(`current pointer: ${start}`)
            if (jqXHR.status == 555) {
              console.log(`Retry slice: ${start}`);
              upload_file(start);
            }

          },
          success: function (data) {
            var size_done = start + chunkSize;
            var percent_done = Math.floor((size_done / file.size) * 100);
            // console.log(percent_done);

            if (next_slice < file.size) {
              // Update upload progress
              console.log(`Uploading File -  ${percent_done}%`);
              progress.attr("value", percent_done);

              // More to upload, call function recursively
              upload_file(next_slice);
            } else {
              // Update upload progress
              console.log('Upload Complete!');
              progress.attr("value", 100);
            }
          }
        });
      };

      reader.readAsDataURL(blob);
      // reader.readAsText(blob);
      // reader.readAsArrayBuffer(blob);
    }



  }); // #upload.change()

  $("#download").click(function () {
    file = $("#downloadFile").val()
    if (file === "") {
      alert("Input file path")
      return
    }

    // Chrome save dialog will open after file has been downloaded
    // fetch(`download?filepath=${file}&minion=${getSession("minion")}`)
    // .then((resp) =>{
    //   if (!resp.ok) {
    //     alert(`${file} not exist`)
    //   } else {
    //     resp.blob().then((blob) => {
    //       let url = window.URL.createObjectURL(blob);
    //       let a = document.createElement('a');
    //       a.style.display = 'none';
    //       a.href = url;
    //       a.download = file.split('/').pop();
    //       document.body.appendChild(a);
    //       a.click();
    //       window.URL.revokeObjectURL(url)
    //     })
    //   }
    // })
    // .catch((err) => {
    //   alert(err)
    // })

    // With Chrome download progress
    // window.location.href = `download?filepath=${file}&minion=${getSession("minion")}`;
    window.open(`download?filepath=${file}&minion=${getSession("minion")}`);
  }); // #download.click()

  menu.click(function () {
    $("#downloadFile").val("");
    progress.hide();
    toolbar.toggle();
    info.text("")
  })

  $(window).on('beforeunload', function (evt) {
    console.log(evt);
    // Use 'beforeunload' to prevent "ctrl+W" from closing browser tab
    return "bye";
  });

  $(document).on('click', '#connect a', function (event) {
    var port = $(this).data("value");
    console.log(`Connect to port: ${port}`);
    // Set port filed value
    $('#port').val(port);

    $('#login-dialog').css('left', event.pageX);
    $('#login-dialog').css('top', event.pageY);
    document.getElementById('login-dialog').showModal();
  });

  clean.click(function () {
    $.ajax({
      url: '/clean',
      type: 'GET',

    }).done(function (resp) {
      clients = JSON.parse(resp);
      fillClientsTable(clients);
    }).fail(function (resp) {
      console.log('Clean clients failed');
      console.log(resp.status);
      console.log(resp);
    });
  });

}); // jQuery
