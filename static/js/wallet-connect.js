/*
 * Wallet connect module leveraging Ethers v6 with optional Web3Modal hook.
 * Implements Phase 1 UI-only wallet connect flow with session persistence.
 */

const walletModule = (() => {
  const selectors = {
    connectButton: '[data-wallet-connect] button',
    addressTarget: '[data-wallet-address]'
  };

  let modal;
  let provider;
  let signer;
  let walletAddress;
  let saving = false;

  const api = {
    async init() {
      if (!window.ethers) {
        console.error('Ethers library missing; wallet connect disabled.');
        return;
      }

      const button = document.querySelector(selectors.connectButton);
      if (!button) return;

      modal = createModal(button.dataset.projectId);

      button.addEventListener('click', api.handleConnectClick);

      await api.hydrateFromServer(button);

      if (window.ethereum) {
        window.ethereum.on?.('accountsChanged', api.handleAccountsChanged);
      }
    },

    async hydrateFromServer(button) {
      try {
        const response = await fetch('/authz/session');
        const body = await response.json();
        if (body?.ok && body.data?.address) {
          walletAddress = body.data.address;
          api.renderConnectedState();
          button.dataset.connectedAddress = walletAddress;
        } else {
          api.renderDisconnectedState();
        }
      } catch (error) {
        console.warn('Unable to hydrate wallet session', error);
        api.renderDisconnectedState();
      }
    },

    async handleConnectClick(event) {
      event.preventDefault();

      try {
        if (modal && modal.openModal) {
          await modal.openModal();
          const modalProvider = await modal.getWalletProvider?.();
          if (modalProvider) {
            provider = new window.ethers.BrowserProvider(modalProvider);
          }
        }

        if (!provider) {
          const injectedProvider = window.ethereum;
          if (!injectedProvider) {
            throw new Error('No Ethereum provider detected.');
          }
          await injectedProvider.request?.({ method: 'eth_requestAccounts' });
          provider = new window.ethers.BrowserProvider(injectedProvider);
        }

        signer = await provider.getSigner();
        walletAddress = await signer.getAddress();

        await api.persistWallet(walletAddress);
        api.renderConnectedState();
      } catch (error) {
        console.error('Wallet connect failed', error);
        api.renderDisconnectedState('Connect wallet');
      }
    },

    async handleDisconnectClick(event) {
      event.preventDefault();
      walletAddress = undefined;
      provider = undefined;
      signer = undefined;
      await api.clearWallet();
      api.renderDisconnectedState();
    },

    async handleAccountsChanged(accounts) {
      if (!accounts || accounts.length === 0) {
        await api.handleDisconnectClick(new Event('dummy'));
        return;
      }

      walletAddress = accounts[0];
      await api.persistWallet(walletAddress);
      api.renderConnectedState();
    },

    async persistWallet(address) {
      if (saving) return;
      saving = true;
      try {
        await fetch('/authz/session', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ address })
        });
      } catch (error) {
        console.error('Unable to persist wallet session', error);
      } finally {
        saving = false;
      }
    },

    async clearWallet() {
      try {
        await fetch('/authz/session', {
          method: 'DELETE'
        });
      } catch (error) {
        console.error('Unable to clear wallet session', error);
      }
    },

    renderConnectedState() {
      const button = document.querySelector(selectors.connectButton);
      const addressTarget = document.querySelector(selectors.addressTarget);
      if (!button || !addressTarget) return;

      button.textContent = 'Disconnect';
      button.classList.add('ui-button--secondary');
      button.classList.remove('ui-button--primary');
      button.removeEventListener('click', api.handleConnectClick);
      button.addEventListener('click', api.handleDisconnectClick);
      button.dataset.connectedAddress = walletAddress;

      addressTarget.textContent = shortAddress(walletAddress);
      addressTarget.dataset.state = 'connected';
    },

    renderDisconnectedState(label = 'Connect wallet') {
      const button = document.querySelector(selectors.connectButton);
      const addressTarget = document.querySelector(selectors.addressTarget);
      if (!button || !addressTarget) return;

      button.textContent = label;
      button.classList.add('ui-button--primary');
      button.classList.remove('ui-button--secondary');
      button.removeEventListener('click', api.handleDisconnectClick);
      button.addEventListener('click', api.handleConnectClick);
      delete button.dataset.connectedAddress;

      addressTarget.textContent = 'Not connected';
      addressTarget.dataset.state = 'disconnected';
    }
  };

  function createModal(projectId) {
    if (!projectId) {
      console.info('WALLETCONNECT_PROJECT_ID not set; falling back to injected provider.');
      return undefined;
    }

    const Web3ModalCtor = window.Web3Modal?.default || window.Web3Modal;
    if (Web3ModalCtor) {
      try {
        return new Web3ModalCtor({
          projectId,
          themeMode: 'dark',
          enableAnalytics: false
        });
      } catch (error) {
        console.warn('Failed to instantiate Web3Modal', error);
      }
    }

    return undefined;
  }

  function shortAddress(address) {
    if (!address || address.length < 10) return address || '';
    return `${address.slice(0, 6)}…${address.slice(-4)}`;
  }

  document.addEventListener('DOMContentLoaded', api.init);

  return api;
})();
