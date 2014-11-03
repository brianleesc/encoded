/** @jsx React.DOM */
'use strict';
var React = require('react');
var url = require('url');
var mixins = require('./mixins');
var productionHost = require('./globals').productionHost;
var _ = require('underscore');
var Navbar = require('react-bootstrap/Navbar');
var Nav = require('react-bootstrap/Nav');
var NavItem = require('react-bootstrap/NavItem');
var DropdownButton = require('react-bootstrap/DropdownButton');
var MenuItem = require('react-bootstrap/MenuItem');

// Hide data from NavBarLayout
var NavBar = React.createClass({
    render: function() {
        var section = url.parse(this.props.href).pathname.split('/', 2)[1] || '';
        return NavBarLayout({
            loadingComplete: this.props.loadingComplete,
            portal: this.props.portal,
            section: section,
            session: this.props.session,
            context_actions: this.props.context_actions,
            user_actions: this.props.user_actions,
            href: this.props.href,
            navigate: this.props.navigate
        });
    }
});


var NavBarLayout = React.createClass({
    getInitialState: function() {
        return {
            testWarning: !productionHost[url.parse(this.props.href).hostname]
        };
    },

    handleClick: function(e) {
        e.preventDefault();
        e.stopPropagation();

        // Remove the warning banner because the user clicked the close icon
        this.setState({testWarning: false});

        // If collection with .sticky-header on page, jiggle scroll position
        // to force the sticky header to jump to the top of the page.
        var hdrs = document.getElementsByClassName('sticky-header');
        if (hdrs.length) {
            window.scrollBy(0,-1);
            window.scrollBy(0,1);
        }
    },

    render: function() {
        console.log('render navbar');
        var portal = this.props.portal;
        var section = this.props.section;
        var session = this.props.session;
        var user_actions = this.props.user_actions;
        var context_actions = this.props.context_actions;
        return (
            <div id="navbar" className="navbar navbar-fixed-top navbar-inverse">
                <div className="container">
                    <Navbar brand={<a href="/">{portal.portal_title}</a>} toggleNavKey={1} bsClass="nav" bsStyle="link">
                        <div key={1}>
                            <GlobalSections collapsable={this.props.collapsable} expanded={this.props.expanded} global_sections={portal.global_sections} section={section} navigate={this.props.navigate} />
                            {this.transferPropsTo(<UserActions />)}
                            {context_actions ? this.transferPropsTo(<ContextActions />) : null}
                            {this.transferPropsTo(<Search />)}
                        </div>
                    </Navbar>
                </div>
                {this.state.testWarning ?
                    <div className="test-warning">
                        <div className="container">
                            <p>
                                The data displayed on this page is not official and only for testing purposes.
                                <a href="#" className="test-warning-close icon icon-times-circle-o" onClick={this.handleClick}></a>
                            </p>
                        </div>
                    </div>
                : null}
            </div>
        );
    }
});


var GlobalSections = React.createClass({
    // So that react-bootstrap closes the menu after you choose an item,
    // we need to supply it with this function which goes to the item's link.
    handleSelect: function(dest) {
        return function() {
            this.props.navigate(dest);
        }.bind(this);
    },

    render: function() {
        var section = this.props.section;

        // Render top-level main menu
        var actions = this.props.global_sections.map(function (action) {
            var subactions;
            if (action.children) {
                // Has dropdown menu; render it into subactions var
                subactions = action.children.map(function (action) {
                    return (
                        <MenuItem href={action.url || ''} key={action.id} onSelect={this.handleSelect(action.url)}>
                            {action.title}
                        </MenuItem>
                    );
                }.bind(this));
            }
            if (action.children) {
                return (
                    <DropdownButton navItem key={action.id} title={action.title}>
                        {subactions}
                    </DropdownButton>
                );
            } else {
                return (
                    <NavItem key={action.id} href={action.url || ''}>
                        {action.title}
                    </NavItem>
                );
            }
        }.bind(this));
        return <Nav navbar>{actions}</Nav>;
    }
});

var ContextActions = React.createClass({
    render: function() {
        var actions = this.props.context_actions.map(function(action) {
            return (
                <MenuItem href={action.href} key={action.name}>
                    <i className="icon icon-pencil"></i> {action.title}
                </MenuItem>
            );
        });
        if (this.props.context_actions.length > 1) {
            actions = (
                <DropdownButton navItem>
                    <i className="icon icon-gear"></i>
                    <Nav navbar={true} dropdown={true}>
                        {actions}
                    </Nav>
                </DropdownButton>
            );
        }
        return <Nav navbar pullRight id="edit-actions">{actions}</Nav>;
    }
});

var Search = React.createClass({
    render: function() {
        var id = url.parse(this.props.href, true);
        var searchTerm = id.query['searchTerm'] || '';
        return (
            <form className="navbar-form navbar-right" action="/search/">
                <div className="search-wrapper">
                    <input className="form-control search-query" id="navbar-search" type="text" placeholder="Search ENCODE" 
                        ref="searchTerm" name="searchTerm" defaultValue={searchTerm} key={searchTerm} />
                </div>
            </form>
        );  
    }
});


var UserActions = React.createClass({
    // Functions to login or logout using Persona
    contextTypes: {
        triggerLogin: React.PropTypes.func, // Login through Persona
        triggerLogout: React.PropTypes.func // Logout through Persona
    },

    render: function() {
        var session = this.props.session;
        var disabled = !this.props.loadingComplete;
        if (!(session && session['auth.userid'])) {
            return (
                <Nav navbar={true} pullRight={true} id="user-actions">
                    <NavItem onClick={this.context.triggerLogin} disabled={disabled}>Sign in</NavItem>
                </Nav>
            );
        }
        var actions = this.props.user_actions.map(function (action) {
            return (
                <MenuItem href={action.url || ''} key={action.id} data-bypass={action.bypass} onClick={this.context[action.trigger]}>
                    {action.title}
                </MenuItem>
            );
        }.bind(this));
        var fullname = (session.user_properties && session.user_properties.title) || 'unknown';
        return (
            <Nav navbar={true} pullRight={true} id="user-actions">
                <DropdownButton title={fullname}>
                    {actions}
                </DropdownButton>
            </Nav>
        );
    }
});

module.exports = NavBar;
